from collections.abc import Awaitable, Callable

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.kafka import options as kafka_options
from app.core.errors import DomainError
from app.models.entities import KafkaCluster, PipelineDestination, WarehouseBatchLedger
from app.services.azure_synapse.planner import plan_batch
from app.services.warehouses.format import serialize_history_csv
from app.services.warehouses.kafka import commit_map, normalize_event
from app.services.warehouses.ledger import checkpoint_ready, fail, transition

Stage = Callable[[str, bytes, dict], Awaitable[dict]]
Apply = Callable[[str, str], Awaitable[None]]
Marker = Callable[[str], Awaitable[bool]]
Classify = Callable[[Exception], dict]


async def process_staged_once(
    session: AsyncSession,
    delivery: PipelineDestination,
    kafka: KafkaCluster,
    kafka_credentials: dict[str, str],
    *,
    provider: str,
    staging_prefix: str,
    stage: Stage,
    apply: Apply,
    marker_exists: Marker,
    classify: Classify,
) -> dict:
    """Run one recoverable batch without advancing Kafka before destination apply."""
    config = delivery.configuration_json
    consumer = AIOKafkaConsumer(
        *(mapping["topic"] for mapping in delivery.topic_mapping_json),
        group_id=f"cluecdc-{provider.lower()}-{delivery.id.hex}",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        **kafka_options(kafka, kafka_credentials),
    )
    await consumer.start()
    try:
        recovery = await session.scalar(
            select(WarehouseBatchLedger)
            .where(
                WarehouseBatchLedger.delivery_id == delivery.id,
                WarehouseBatchLedger.status == "APPLIED",
            )
            .order_by(WarehouseBatchLedger.created_at)
            .limit(1)
        )
        if recovery is not None:
            await consumer.getmany(timeout_ms=500, max_records=1)
            if not await marker_exists(recovery.batch_id):
                fail(recovery, f"{provider}_DESTINATION_MARKER_MISSING", retryable=False)
                await session.commit()
                raise DomainError(
                    f"{provider}_DESTINATION_MARKER_MISSING",
                    "Applied ledger batch has no destination marker; checkpoint is blocked",
                    409,
                )
            await consumer.commit(commit_map(recovery.source_offsets_json))
            transition(recovery, "CHECKPOINTED")
            await session.commit()
            return {"status": "CHECKPOINTED", "batch_id": recovery.batch_id, "recovered": True}

        polled = await consumer.getmany(
            timeout_ms=int(config.get("batch_flush_interval_ms", 1000)),
            max_records=int(config.get("batch_size", 10000)),
        )
        events = [normalize_event(record) for records in polled.values() for record in records]
        if not events:
            return {"status": "IDLE", "records": 0}
        plan = plan_batch(str(delivery.id), events, staging_prefix)
        batch = await session.scalar(
            select(WarehouseBatchLedger).where(
                WarehouseBatchLedger.delivery_id == delivery.id,
                WarehouseBatchLedger.batch_id == plan.batch_id,
            )
        )
        if batch is None:
            batch = WarehouseBatchLedger(
                delivery_id=delivery.id,
                batch_id=plan.batch_id,
                source_offsets_json=plan.source_offsets,
                staging_manifest_json={},
                status="CREATED",
            )
            session.add(batch)
            await session.commit()
        if batch.status == "CHECKPOINTED":
            await consumer.commit(commit_map(batch.source_offsets_json))
            return {"status": "CHECKPOINTED", "batch_id": batch.batch_id, "replayed": True}
        if batch.status == "TERMINAL_FAILED":
            raise DomainError(
                batch.last_error_code or f"{provider}_BATCH_TERMINAL",
                "A terminal warehouse batch failure requires operator intervention",
                409,
            )
        try:
            if batch.status != "APPLIED":
                if batch.status in {"CREATED", "RETRYABLE_FAILED"}:
                    transition(batch, "STAGING")
                    await session.commit()
                if batch.status == "STAGING":
                    body = serialize_history_csv(events, batch.batch_id)
                    manifest = await stage(
                        plan.staging_key,
                        body,
                        {
                            "version": 1,
                            "batch_id": batch.batch_id,
                            "delivery_id": str(delivery.id),
                            "record_count": len(events),
                            "source_offsets": plan.source_offsets,
                        },
                    )
                    batch.staging_manifest_json = manifest
                    transition(batch, "STAGED")
                    await session.commit()
                manifest = batch.staging_manifest_json
                if not manifest.get("data_uri"):
                    raise DomainError(
                        f"{provider}_STAGING_MANIFEST_INVALID",
                        "Staged batch has no durable data URI",
                        409,
                    )
                if batch.status == "STAGED":
                    transition(batch, "APPLYING")
                    await session.commit()
                await apply(batch.batch_id, str(manifest["data_uri"]))
                if not await marker_exists(batch.batch_id):
                    raise DomainError(
                        f"{provider}_DESTINATION_MARKER_MISSING",
                        "Warehouse apply completed without a durable destination marker",
                        503,
                    )
                transition(batch, "APPLIED")
                await session.commit()
            if not checkpoint_ready(batch):
                raise DomainError(
                    f"{provider}_CHECKPOINT_BLOCKED",
                    "Kafka checkpoint is blocked until destination apply is durable",
                    409,
                )
            await consumer.commit(commit_map(batch.source_offsets_json))
            transition(batch, "CHECKPOINTED")
            await session.commit()
            return {
                "status": "CHECKPOINTED",
                "batch_id": batch.batch_id,
                "records": len(events),
            }
        except Exception as exc:
            if batch.status not in {"APPLIED", "CHECKPOINTED", "TERMINAL_FAILED"}:
                classification = classify(exc)
                fail(batch, classification["code"], retryable=classification["retryable"])
                await session.commit()
            raise
    finally:
        await consumer.stop()
