import time
from collections.abc import Awaitable, Callable

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import TopicPartition
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


async def _recover_events(consumer, offsets: dict) -> list[dict]:
    """Re-read exactly the persisted staging range, never a newly sized poll."""
    ranges = {}
    for coordinate, bounds in offsets.items():
        topic, partition = coordinate.rsplit(":", 1)
        ranges[TopicPartition(topic, int(partition))] = bounds
    deadline = time.monotonic() + 30
    while not set(ranges) <= consumer.assignment():
        if time.monotonic() >= deadline:
            raise DomainError(
                "WAREHOUSE_RECOVERY_ASSIGNMENT_UNAVAILABLE",
                "Original batch partitions are unavailable; checkpoint remains unchanged",
                503,
            )
        await consumer.getmany(timeout_ms=500, max_records=1)
    beginnings = await consumer.beginning_offsets(list(ranges))
    if any(beginnings[tp] > bounds["first"] for tp, bounds in ranges.items()):
        raise DomainError(
            "WAREHOUSE_RECOVERY_HISTORY_EXPIRED",
            "Kafka no longer retains the pending batch; explicit operator recovery is required",
            409,
        )
    for tp, bounds in ranges.items():
        consumer.seek(tp, bounds["first"])
    events = {}
    completed: set[TopicPartition] = set()
    while completed != set(ranges):
        if time.monotonic() >= deadline:
            raise DomainError(
                "WAREHOUSE_RECOVERY_RANGE_INCOMPLETE",
                "Original batch could not be read completely; checkpoint remains unchanged",
                503,
            )
        polled = await consumer.getmany(timeout_ms=500, max_records=10000)
        for tp, records in polled.items():
            if tp not in ranges:
                continue
            bounds = ranges[tp]
            for record in records:
                if bounds["first"] <= record.offset <= bounds["last"]:
                    events[(tp, record.offset)] = normalize_event(record)
                if record.offset >= bounds["last"]:
                    completed.add(tp)
    if any(
        (tp, bound) not in events
        for tp, bounds in ranges.items()
        for bound in (bounds["first"], bounds["last"])
    ):
        raise DomainError(
            "WAREHOUSE_RECOVERY_RANGE_INCOMPLETE",
            "Original batch boundary records are missing; checkpoint remains unchanged",
            409,
        )
    return list(events.values())


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
    consumer_group_prefix: str | None = None,
) -> dict:
    """Run one recoverable batch without advancing Kafka before destination apply."""
    config = delivery.configuration_json
    consumer = AIOKafkaConsumer(
        *(mapping["topic"] for mapping in delivery.topic_mapping_json),
        group_id=f"{consumer_group_prefix or ('cluecdc-' + provider.lower())}-{delivery.id.hex}",
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
                WarehouseBatchLedger.status != "CHECKPOINTED",
            )
            .order_by(WarehouseBatchLedger.created_at)
            .limit(1)
        )
        if recovery is not None and recovery.status == "APPLIED":
            await consumer.getmany(timeout_ms=500, max_records=1)
            if not await marker_exists(recovery.batch_id):
                # Preserve APPLIED: destination unavailability must not erase the
                # durable apply state or permit a fresh batch to bypass it.
                recovery.last_error_code = f"{provider}_DESTINATION_MARKER_MISSING"
                await session.commit()
                raise DomainError(
                    f"{provider}_DESTINATION_MARKER_MISSING",
                    "Applied ledger batch has no destination marker; checkpoint is blocked",
                    409,
                )
            await consumer.commit(commit_map(recovery.source_offsets_json))
            recovery.last_error_code = None
            transition(recovery, "CHECKPOINTED")
            await session.commit()
            return {"status": "CHECKPOINTED", "batch_id": recovery.batch_id, "recovered": True}

        events = []
        batch = recovery
        if batch is None:
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
                staging_manifest_json={"record_count": len(events)},
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
                    # An upload/apply may have succeeded before the process died.
                    # Recover the existing manifest rather than silently widening
                    # the batch to a different poll and defeating marker dedup.
                    if batch.status == "RETRYABLE_FAILED" and batch.staging_manifest_json.get(
                        "data_uri"
                    ):
                        transition(batch, "APPLYING")
                    else:
                        transition(batch, "STAGING")
                    await session.commit()
                if batch.status == "STAGING":
                    if recovery is not None:
                        if "record_count" not in batch.staging_manifest_json:
                            raise DomainError(
                                "WAREHOUSE_RECOVERY_COUNT_UNAVAILABLE",
                                "Pending batch has no persisted record count; explicit operator "
                                "reconciliation is required before restaging",
                                409,
                            )
                        events = await _recover_events(consumer, batch.source_offsets_json)
                        count = batch.staging_manifest_json.get("record_count")
                        if count is not None and len(events) != count:
                            raise DomainError(
                                "WAREHOUSE_RECOVERY_RANGE_INCOMPLETE",
                                "Original batch records are missing; checkpoint remains unchanged",
                                409,
                            )
                    plan = plan_batch(str(delivery.id), events, staging_prefix)
                    if plan.batch_id != batch.batch_id:
                        raise DomainError(
                            "WAREHOUSE_RECOVERY_RANGE_MISMATCH",
                            "Recovered events do not match the original batch identity",
                            409,
                        )
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
                classification: dict = (
                    {"code": exc.code, "retryable": exc.status >= 500}
                    if isinstance(exc, DomainError) and exc.code.startswith("WAREHOUSE_RECOVERY_")
                    else classify(exc)
                )
                fail(batch, classification["code"], retryable=classification["retryable"])
                await session.commit()
            raise
    finally:
        await consumer.stop()
