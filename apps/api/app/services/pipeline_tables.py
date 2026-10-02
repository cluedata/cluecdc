import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.connect import KafkaConnectClient
from app.adapters.kafka import KafkaExplorer
from app.core.errors import DomainError, redact
from app.models.entities import (
    ConnectCluster,
    Connector,
    Destination,
    Job,
    KafkaCluster,
    Pipeline,
    PipelineDestination,
    PipelineOperation,
    PipelineTable,
    Source,
    SourceTable,
    now,
)
from app.providers import get_provider
from app.repositories.metadata import audit, get, serialize
from app.schemas.requests import (
    AddPipelineTablesInput,
    DeliveryInput,
    RemovePipelineTableInput,
    ResyncPipelineTableInput,
    TopicMapping,
)
from app.services import pipeline as pipeline_service
from app.services.debezium import notification_topic
from app.services.destinations import service as destination_service
from app.services.source import adapter as source_adapter

ADD_STEPS = [
    "validate_source",
    "prepare_destination",
    "update_publication",
    "update_source_connector",
    "wait_source_healthy",
    "update_sink",
    "trigger_snapshot",
    "wait_snapshot",
    "verify_destination",
    "complete",
]
REMOVE_STEPS = [
    "stop_snapshot",
    "update_source_connector",
    "update_publication",
    "update_sink",
    "destination_cleanup",
    "complete",
]
RESYNC_STEPS = [
    "validate_source",
    "validate_connector",
    "trigger_snapshot",
    "wait_snapshot",
    "verify_destination",
    "complete",
]


async def _assert_no_active_operation(session: AsyncSession, table_id: uuid.UUID) -> None:
    active = await session.scalar(
        select(PipelineOperation.id).where(
            PipelineOperation.table_id == table_id,
            PipelineOperation.status.in_(["PENDING", "RUNNING", "WAITING"]),
        )
    )
    if active:
        raise DomainError(
            "TABLE_OPERATION_ACTIVE", "This table already has an active operation", 409
        )


async def enqueue_add(
    session: AsyncSession,
    pipeline_id: uuid.UUID,
    data: AddPipelineTablesInput,
    actor: str,
) -> list[PipelineOperation]:
    pipeline = await pipeline_service.locked(session, pipeline_id)
    if not pipeline.connector_id:
        raise DomainError("PIPELINE_NOT_DEPLOYED", "Deploy the pipeline before adding tables", 409)
    existing = {
        (table.schema_name, table.table_name): table
        for table in (
            await session.scalars(
                select(PipelineTable).where(PipelineTable.pipeline_id == pipeline_id)
            )
        ).all()
    }
    discovered = {
        (table.schema_name, table.table_name): table
        for table in (
            await session.scalars(
                select(SourceTable).where(SourceTable.source_id == pipeline.source_id)
            )
        ).all()
    }
    operations = []
    for request in data.tables:
        key = (request.schema_name, request.table_name)
        current = existing.get(key)
        if current and current.cdc_status != "REMOVED":
            raise DomainError(
                "TABLE_ALREADY_CAPTURED",
                f"{request.schema_name}.{request.table_name} is already in this pipeline",
                409,
            )
        source_table = discovered.get(key)
        if source_table is None:
            raise DomainError(
                "TABLE_NOT_DISCOVERED", "Discover the source before adding this table", 422
            )
        if current:
            table = current
            table.removed_at = None
            table.cdc_status = "PENDING"
        else:
            table = PipelineTable(
                pipeline_id=pipeline_id,
                schema_name=request.schema_name,
                table_name=request.table_name,
                topic_name=f"{pipeline.topic_prefix}.{request.schema_name}.{request.table_name}",
            )
            session.add(table)
        table.primary_key_columns = request.custom_key_columns or source_table.primary_key_columns
        table.destination_schema = request.destination_schema or request.schema_name
        table.destination_table = request.destination_table or request.table_name
        table.initial_data_strategy = request.initial_data_strategy
        table.snapshot_status = (
            "PENDING" if request.initial_data_strategy == "BACKFILL" else "NOT_REQUIRED"
        )
        table.schema_status = "IN_SYNC"
        table.destination_status = "READY"
        await session.flush()
        operation = PipelineOperation(
            pipeline_id=pipeline_id,
            table_id=table.id,
            type="ADD_TABLE",
            status="PENDING",
            current_step=ADD_STEPS[0],
            progress=0,
            metadata_json={
                "actor": actor,
                "steps": ADD_STEPS,
                "completed_steps": [],
                "destination_handling": request.destination_handling,
            },
        )
        session.add(operation)
        await session.flush()
        session.add(Job(kind="pipeline_operation", resource_id=operation.id, actor=actor))
        audit(session, actor, "pipeline.table_add_requested", operation)
        operations.append(operation)
    return operations


async def enqueue_resync(
    session: AsyncSession,
    table_id: uuid.UUID,
    data: ResyncPipelineTableInput,
    actor: str,
) -> PipelineOperation:
    table = await get(session, PipelineTable, table_id)
    if table.cdc_status == "REMOVED":
        raise DomainError("TABLE_REMOVED", "Removed tables cannot be resynced", 409)
    await _assert_no_active_operation(session, table.id)
    operation = PipelineOperation(
        pipeline_id=table.pipeline_id,
        table_id=table.id,
        type="RESYNC_TABLE",
        status="PENDING",
        current_step=RESYNC_STEPS[0],
        progress=0,
        metadata_json={
            "actor": actor,
            "steps": RESYNC_STEPS,
            "completed_steps": [],
            "scope": data.scope,
        },
    )
    session.add(operation)
    await session.flush()
    session.add(Job(kind="pipeline_operation", resource_id=operation.id, actor=actor))
    audit(session, actor, "pipeline.table_resync_requested", operation)
    return operation


async def enqueue_remove(
    session: AsyncSession,
    table_id: uuid.UUID,
    data: RemovePipelineTableInput,
    actor: str,
) -> PipelineOperation:
    table = await get(session, PipelineTable, table_id)
    if table.cdc_status == "REMOVED":
        raise DomainError("TABLE_REMOVED", "Table is already removed", 409)
    remaining = await session.scalar(
        select(PipelineTable.id)
        .where(
            PipelineTable.pipeline_id == table.pipeline_id,
            PipelineTable.id != table.id,
            PipelineTable.cdc_status.not_in(["REMOVING", "REMOVED"]),
        )
        .limit(1)
    )
    if remaining is None:
        raise DomainError(
            "LAST_TABLE_REMOVAL",
            "Delete the pipeline to remove its final captured table",
            409,
        )
    await _assert_no_active_operation(session, table.id)
    table.cdc_status = "REMOVING"
    operation = PipelineOperation(
        pipeline_id=table.pipeline_id,
        table_id=table.id,
        type="REMOVE_TABLE",
        status="PENDING",
        current_step=REMOVE_STEPS[0],
        progress=0,
        metadata_json={
            "actor": actor,
            "steps": REMOVE_STEPS,
            "completed_steps": [],
            "destination_handling": data.destination_handling,
        },
    )
    session.add(operation)
    await session.flush()
    session.add(Job(kind="pipeline_operation", resource_id=operation.id, actor=actor))
    audit(session, actor, "pipeline.table_remove_requested", operation)
    return operation


async def run_operation(session: AsyncSession, operation_id: uuid.UUID) -> dict:
    operation = await session.scalar(
        select(PipelineOperation).where(PipelineOperation.id == operation_id).with_for_update()
    )
    if operation is None:
        raise DomainError("NOT_FOUND", "Pipeline operation was not found", 404)
    if operation.status in {"SUCCEEDED", "CANCELLED"}:
        return serialize(operation)
    operation.status = "RUNNING"
    operation.started_at = operation.started_at or now()
    await session.commit()
    try:
        if operation.type == "ADD_TABLE":
            await _run_add(session, operation)
        elif operation.type == "REMOVE_TABLE":
            await _run_remove(session, operation)
        elif operation.type == "RESYNC_TABLE":
            await _run_resync(session, operation)
        else:
            raise DomainError("UNKNOWN_OPERATION", "Unsupported pipeline operation type")
        return serialize(operation)
    except Exception as exc:
        await session.rollback()
        operation = await get(session, PipelineOperation, operation_id)
        operation.status = "FAILED"
        operation.error_code = exc.code if isinstance(exc, DomainError) else "OPERATION_FAILED"
        operation.error_message = (
            exc.message
            if isinstance(exc, DomainError)
            else "Operation failed; inspect correlation logs"
        )
        operation.finished_at = now()
        if operation.table_id:
            table = await get(session, PipelineTable, operation.table_id)
            if operation.type == "REMOVE_TABLE":
                table.cdc_status = "FAILED"
            elif operation.type in {"ADD_TABLE", "RESYNC_TABLE"}:
                table.snapshot_status = "FAILED"
        audit(
            session, operation.metadata_json.get("actor", "worker"), "operation.failed", operation
        )
        await session.commit()
        raise


async def _checkpoint(
    session: AsyncSession, operation: PipelineOperation, step: str, steps: list[str]
) -> None:
    metadata = dict(operation.metadata_json)
    complete = list(metadata.get("completed_steps", []))
    if step not in complete:
        complete.append(step)
    metadata["completed_steps"] = complete
    operation.metadata_json = metadata
    index = steps.index(step) + 1
    operation.progress = int(index / len(steps) * 100)
    operation.current_step = steps[index] if index < len(steps) else "complete"
    audit(session, metadata.get("actor", "worker"), "operation.step_completed", operation)
    await session.commit()


def _completed(operation: PipelineOperation, step: str) -> bool:
    return step in operation.metadata_json.get("completed_steps", [])


async def _context(session: AsyncSession, operation: PipelineOperation):
    pipeline = await get(session, Pipeline, operation.pipeline_id)
    if not operation.table_id:
        raise DomainError("TABLE_REQUIRED", "Operation has no pipeline table")
    table = await get(session, PipelineTable, operation.table_id)
    source = await get(session, Source, pipeline.source_id)
    database = await source_adapter(session, source)
    connector = (
        await get(session, Connector, pipeline.connector_id) if pipeline.connector_id else None
    )
    cluster = await get(session, ConnectCluster, pipeline.connect_cluster_id)
    return pipeline, table, source, database, connector, cluster


async def _run_add(session: AsyncSession, operation: PipelineOperation) -> None:
    pipeline, table, source, database, connector, cluster = await _context(session, operation)
    actor = operation.metadata_json.get("actor", "worker")
    if connector is None:
        raise DomainError("PIPELINE_NOT_DEPLOYED", "Pipeline source connector is missing", 409)
    if not _completed(operation, "validate_source"):
        readiness = await database.inspect_table(table.schema_name, table.table_name)
        if not readiness["ready"]:
            raise DomainError(
                "TABLE_NOT_READY",
                f"{table.schema_name}.{table.table_name} is not ready for CDC",
                422,
                readiness,
            )
        if table.initial_data_strategy == "BACKFILL" and not readiness["primary_key_columns"]:
            raise DomainError(
                "INCREMENTAL_SNAPSHOT_REQUIRES_KEY",
                "Backfill requires a primary key; choose future changes only or add a key",
                422,
                readiness,
            )
        table.primary_key_columns = table.primary_key_columns or readiness["primary_key_columns"]
        table.snapshot_estimated_rows = readiness["estimated_rows"]
        table.cdc_status = "ACTIVATING"
        metadata = dict(operation.metadata_json)
        metadata["readiness"] = readiness
        operation.metadata_json = metadata
        await _checkpoint(session, operation, "validate_source", ADD_STEPS)
    if not _completed(operation, "prepare_destination"):
        kafka = await get(session, KafkaCluster, pipeline.kafka_cluster_id)
        await KafkaExplorer().ensure_topics(
            kafka, [table.topic_name, notification_topic(pipeline.topic_prefix)]
        )
        await _prepare_destinations(session, pipeline, table, operation)
        await _checkpoint(session, operation, "prepare_destination", ADD_STEPS)
    provider = get_provider(source.type)
    signals = provider.signal_service(database, source, pipeline.topic_prefix)
    signal_collection = await signals.ensure_table()
    publication = provider.publication_manager(database, connector.config_json)
    if not _completed(operation, "update_publication"):
        signal_schema, signal_table = signal_collection.split(".", 1)
        await publication.add_table(signal_schema, signal_table)
        await publication.add_table(table.schema_name, table.table_name)
        await _checkpoint(session, operation, "update_publication", ADD_STEPS)
    if not _completed(operation, "update_source_connector"):
        await _update_source_connector(session, pipeline, connector, cluster)
        await _checkpoint(session, operation, "update_source_connector", ADD_STEPS)
    if not _completed(operation, "wait_source_healthy"):
        await _wait_connector_healthy(
            cluster,
            connector.name,
            expected_table_filter=connector.config_json.get("table.include.list"),
        )
        await _checkpoint(session, operation, "wait_source_healthy", ADD_STEPS)
    if not _completed(operation, "update_sink"):
        await _update_sinks(session, pipeline, table, remove=False)
        await _checkpoint(session, operation, "update_sink", ADD_STEPS)
    if not _completed(operation, "trigger_snapshot"):
        if table.initial_data_strategy == "BACKFILL":
            signal_id = await signals.execute_incremental_snapshot(
                pipeline.id, table.schema_name, table.table_name
            )
            metadata = dict(operation.metadata_json)
            metadata["signal_id"] = signal_id
            metadata["notification_topic"] = notification_topic(pipeline.topic_prefix)
            operation.metadata_json = metadata
            table.snapshot_status = "RUNNING"
            table.snapshot_started_at = now()
            table.snapshot_last_activity_at = now()
        else:
            table.snapshot_status = "NOT_REQUIRED"
        table.cdc_status = "STREAMING"
        await _checkpoint(session, operation, "trigger_snapshot", ADD_STEPS)
    if table.initial_data_strategy == "BACKFILL":
        operation.status = "WAITING"
        operation.current_step = "wait_snapshot"
        await session.commit()
        return
    await _finish(session, operation, table, ADD_STEPS)
    audit(session, actor, "pipeline.table_added", table)
    await session.commit()


async def _run_resync(session: AsyncSession, operation: PipelineOperation) -> None:
    pipeline, table, source, database, connector, cluster = await _context(session, operation)
    provider = get_provider(source.type)
    if connector is None:
        raise DomainError("PIPELINE_NOT_DEPLOYED", "Pipeline source connector is missing", 409)
    if not _completed(operation, "validate_source"):
        readiness = await database.inspect_table(table.schema_name, table.table_name)
        if not readiness["ready"] or not readiness["primary_key_columns"]:
            raise DomainError(
                "TABLE_NOT_READY", "Table is not ready for an incremental snapshot", 422, readiness
            )
        await _checkpoint(session, operation, "validate_source", RESYNC_STEPS)
    if not _completed(operation, "validate_connector"):
        signals = provider.signal_service(database, source, pipeline.topic_prefix)
        signal_collection = await signals.ensure_table()
        signal_schema, signal_table = signal_collection.split(".", 1)
        publication = provider.publication_manager(database, connector.config_json)
        await publication.add_table(signal_schema, signal_table)
        kafka = await get(session, KafkaCluster, pipeline.kafka_cluster_id)
        await KafkaExplorer().ensure_topics(kafka, [notification_topic(pipeline.topic_prefix)])
        if not connector.config_json.get("signal.data.collection"):
            await _update_source_connector(session, pipeline, connector, cluster)
        await _wait_connector_healthy(
            cluster,
            connector.name,
            expected_table_filter=connector.config_json.get("table.include.list"),
        )
        await _checkpoint(session, operation, "validate_connector", RESYNC_STEPS)
    if not _completed(operation, "trigger_snapshot"):
        signals = provider.signal_service(database, source, pipeline.topic_prefix)
        await signals.ensure_table()
        signal_id = await signals.execute_incremental_snapshot(
            pipeline.id, table.schema_name, table.table_name
        )
        metadata = dict(operation.metadata_json)
        metadata["signal_id"] = signal_id
        metadata["notification_topic"] = notification_topic(pipeline.topic_prefix)
        operation.metadata_json = metadata
        table.snapshot_status = "RUNNING"
        table.snapshot_rows_processed = None
        table.snapshot_current_chunk = None
        table.snapshot_started_at = now()
        table.snapshot_finished_at = None
        table.snapshot_last_activity_at = now()
        await _checkpoint(session, operation, "trigger_snapshot", RESYNC_STEPS)
    operation.status = "WAITING"
    operation.current_step = "wait_snapshot"
    await session.commit()


async def _run_remove(session: AsyncSession, operation: PipelineOperation) -> None:
    pipeline, table, source, database, connector, cluster = await _context(session, operation)
    if connector is None:
        raise DomainError("PIPELINE_NOT_DEPLOYED", "Pipeline source connector is missing", 409)
    provider = get_provider(source.type)
    signals = provider.signal_service(database, source, pipeline.topic_prefix)
    if not _completed(operation, "stop_snapshot"):
        if table.snapshot_status in {"RUNNING", "PAUSED"}:
            await signals.stop_snapshot(pipeline.id, table.schema_name, table.table_name)
            table.snapshot_status = "CANCELLED"
            table.snapshot_finished_at = now()
        await _checkpoint(session, operation, "stop_snapshot", REMOVE_STEPS)
    if not _completed(operation, "update_source_connector"):
        await _update_source_connector(session, pipeline, connector, cluster)
        await _wait_connector_healthy(
            cluster,
            connector.name,
            expected_table_filter=connector.config_json.get("table.include.list"),
        )
        await _checkpoint(session, operation, "update_source_connector", REMOVE_STEPS)
    if not _completed(operation, "update_publication"):
        publication = provider.publication_manager(database, connector.config_json)
        await publication.remove_table(table.schema_name, table.table_name)
        await _checkpoint(session, operation, "update_publication", REMOVE_STEPS)
    if not _completed(operation, "update_sink"):
        if operation.metadata_json.get("destination_handling") == "DELETE_TABLE":
            links = (
                await session.scalars(
                    select(PipelineDestination).where(
                        PipelineDestination.pipeline_id == pipeline.id
                    )
                )
            ).all()
            metadata = dict(operation.metadata_json)
            metadata["cleanup_destination_ids"] = [str(link.destination_id) for link in links]
            operation.metadata_json = metadata
        await _update_sinks(session, pipeline, table, remove=True)
        await _checkpoint(session, operation, "update_sink", REMOVE_STEPS)
    if not _completed(operation, "destination_cleanup"):
        if operation.metadata_json.get("destination_handling") == "DELETE_TABLE":
            await _drop_destination_tables(session, operation, table)
        await _checkpoint(session, operation, "destination_cleanup", REMOVE_STEPS)
    table.cdc_status = "REMOVED"
    table.destination_status = "READY"
    table.removed_at = now()
    await _finish(session, operation, table, REMOVE_STEPS)
    audit(session, operation.metadata_json.get("actor", "worker"), "pipeline.table_removed", table)
    await session.commit()


async def _prepare_destinations(
    session: AsyncSession,
    pipeline: Pipeline,
    table: PipelineTable,
    operation: PipelineOperation,
) -> None:
    handling = operation.metadata_json.get("destination_handling", "AUTO_CREATE")
    links = (
        await session.scalars(
            select(PipelineDestination).where(PipelineDestination.pipeline_id == pipeline.id)
        )
    ).all()
    for link in links:
        if link.connector_id is None:
            raise DomainError(
                "DESTINATION_NOT_DEPLOYED",
                "Deploy the destination delivery before adding pipeline tables",
                409,
            )
        destination = await get(session, Destination, link.destination_id)
        mapping = TopicMapping(
            topic=table.topic_name,
            schema_name=table.destination_schema or table.schema_name,
            table_name=table.destination_table or table.table_name,
        )
        settings = dict(link.configuration_json)
        settings.update(
            {
                "pipeline_id": pipeline.id,
                "name": link.name,
                "auto_create": handling == "AUTO_CREATE",
                "auto_evolve": False,
            }
        )
        data = DeliveryInput(**settings, mappings=[mapping])
        _, _, metadata, target = await destination_service.build(
            session, destination, data, (await get(session, Connector, link.connector_id)).name
        )
        if handling == "AUTO_CREATE":
            await target.prepare(data, metadata)
            await target.validate(data, metadata)


async def _update_source_connector(
    session: AsyncSession,
    pipeline: Pipeline,
    connector: Connector,
    cluster: ConnectCluster,
) -> None:
    data = await pipeline_service.input_for(session, pipeline)
    source = await get(session, Source, pipeline.source_id)
    kafka = await get(session, KafkaCluster, pipeline.kafka_cluster_id)
    provider = get_provider(source.type)
    config = provider.build_source_config(
        source,
        data,
        f"${{cluecdc:{source.secret_ref}:password}}",
        connector.name,
        kafka.bootstrap_servers,
        enable_signals=True,
    )
    if any(
        config.get(key) != connector.config_json.get(key)
        for key in provider.immutable_connector_keys()
    ):
        raise DomainError(
            "CONNECTOR_IDENTITY_CHANGED",
            "Table changes must preserve the replication slot and publication",
            409,
        )
    client = KafkaConnectClient(cluster.base_url)
    await client.validate(config)
    await client.update(connector.name, config)
    connector.config_json = redact(config)
    audit(session, "worker", "pipeline.connector_config_updated", connector)


async def _wait_connector_healthy(
    cluster: ConnectCluster,
    connector_name: str,
    expected_table_filter: str | None = None,
) -> None:
    client = KafkaConnectClient(cluster.base_url)
    # A Kafka Connect config PUT returns before the distributed worker has
    # necessarily stopped the old task.  Reading RUNNING immediately can
    # therefore observe the previous task, which may consume and discard a
    # source-table signal before its replacement has the signal table filter.
    # First verify the desired config is visible, then require a short stable
    # RUNNING window before callers are allowed to emit a signal.
    if expected_table_filter is not None:
        for attempt in range(20):
            deployed = await client.config(connector_name)
            if deployed.get("table.include.list") == expected_table_filter:
                break
            if attempt < 19:
                await asyncio.sleep(1)
        else:
            raise DomainError(
                "CONNECTOR_CONFIG_TIMEOUT",
                "Kafka Connect did not publish the requested capture list",
                503,
            )
        await asyncio.sleep(2)

    stable_polls = 0
    for attempt in range(20):
        status = await client.status(connector_name)
        connector_state = status.get("connector", {}).get("state")
        task_states = [task.get("state") for task in status.get("tasks", [])]
        if (
            connector_state == "RUNNING"
            and task_states
            and all(state == "RUNNING" for state in task_states)
        ):
            stable_polls += 1
            if stable_polls >= 2:
                return
        else:
            stable_polls = 0
        if connector_state == "FAILED" or "FAILED" in task_states:
            raise DomainError(
                "CONNECTOR_UNHEALTHY", "Source connector failed after its safe config update", 502
            )
        if attempt < 19:
            await asyncio.sleep(1)
    raise DomainError(
        "CONNECTOR_HEALTH_TIMEOUT",
        "Source connector did not become healthy before the operation timeout",
        503,
    )


async def stop_active_snapshot(
    session: AsyncSession, table_id: uuid.UUID, actor: str
) -> PipelineOperation:
    table = await get(session, PipelineTable, table_id)
    operation = await session.scalar(
        select(PipelineOperation)
        .where(
            PipelineOperation.table_id == table.id,
            PipelineOperation.status == "WAITING",
            PipelineOperation.current_step == "wait_snapshot",
        )
        .order_by(PipelineOperation.created_at.desc())
        .with_for_update()
    )
    if operation is None or table.snapshot_status not in {"RUNNING", "PAUSED"}:
        raise DomainError("SNAPSHOT_NOT_RUNNING", "This table has no active snapshot", 409)

    pipeline = await get(session, Pipeline, table.pipeline_id)
    source = await get(session, Source, pipeline.source_id)
    database = await source_adapter(session, source)
    provider = get_provider(source.type)
    signal_id = await provider.signal_service(
        database, source, pipeline.topic_prefix
    ).stop_snapshot(pipeline.id, table.schema_name, table.table_name)
    metadata = dict(operation.metadata_json)
    metadata["stop_signal_id"] = signal_id
    metadata["stop_requested_at"] = now().isoformat()
    operation.metadata_json = metadata
    operation.status = "CANCELLED"
    operation.current_step = "stop_snapshot"
    operation.finished_at = now()
    table.snapshot_status = "CANCELLED"
    table.snapshot_finished_at = now()
    table.snapshot_last_activity_at = now()
    audit(session, actor, "pipeline.snapshot_stop_requested", operation)
    return operation


async def retry_failed_operation(
    session: AsyncSession, operation: PipelineOperation, actor: str
) -> PipelineOperation:
    if operation.status != "FAILED":
        raise DomainError("OPERATION_NOT_RETRYABLE", "Only failed operations can be retried", 409)
    metadata = dict(operation.metadata_json)
    if operation.type in {"ADD_TABLE", "RESYNC_TABLE"} and "trigger_snapshot" in metadata.get(
        "completed_steps", []
    ):
        restart_steps = {"trigger_snapshot", "wait_snapshot", "verify_destination", "complete"}
        metadata["completed_steps"] = [
            step for step in metadata.get("completed_steps", []) if step not in restart_steps
        ]
        metadata.pop("signal_id", None)
        metadata.pop("stop_signal_id", None)
        operation.current_step = "trigger_snapshot"
        if operation.table_id:
            table = await get(session, PipelineTable, operation.table_id)
            table.snapshot_status = "PENDING"
            table.snapshot_rows_processed = None
            table.snapshot_current_chunk = None
            table.snapshot_finished_at = None
    operation.metadata_json = metadata
    operation.status = "PENDING"
    operation.error_code = None
    operation.error_message = None
    operation.finished_at = None
    session.add(Job(kind="pipeline_operation", resource_id=operation.id, actor=actor))
    audit(session, actor, "operation.retry_requested", operation)
    return operation


async def _update_sinks(
    session: AsyncSession, pipeline: Pipeline, table: PipelineTable, remove: bool
) -> None:
    links = (
        await session.scalars(
            select(PipelineDestination).where(PipelineDestination.pipeline_id == pipeline.id)
        )
    ).all()
    for link in links:
        if not link.connector_id:
            continue
        connector = await get(session, Connector, link.connector_id)
        destination = await get(session, Destination, link.destination_id)
        mappings = [
            TopicMapping(**mapping)
            for mapping in link.topic_mapping_json
            if mapping.get("topic") != table.topic_name
        ]
        if not remove:
            mappings.append(
                TopicMapping(
                    topic=table.topic_name,
                    schema_name=table.destination_schema or table.schema_name,
                    table_name=table.destination_table or table.table_name,
                )
            )
        if not mappings:
            cluster = await get(session, ConnectCluster, connector.connect_cluster_id)
            await KafkaConnectClient(cluster.base_url).operate(connector.name, "delete")
            await session.delete(link)
            await session.flush()
            await session.delete(connector)
            continue
        data = DeliveryInput(
            **{
                **link.configuration_json,
                "pipeline_id": pipeline.id,
                "name": link.name,
                "mappings": mappings,
            }
        )
        config, cluster, _, _ = await destination_service.build(
            session, destination, data, connector.name
        )
        await KafkaConnectClient(cluster.base_url).update(connector.name, config)
        connector.config_json = redact(config)
        link.topic_mapping_json = [mapping.model_dump() for mapping in mappings]
        link.configuration_json = data.model_dump(mode="json", exclude={"mappings"})


async def _drop_destination_tables(
    session: AsyncSession, operation: PipelineOperation, table: PipelineTable
) -> None:
    for identifier in operation.metadata_json.get("cleanup_destination_ids", []):
        destination = await get(session, Destination, uuid.UUID(identifier))
        target = await destination_service.adapter(session, destination)
        await target.drop_table(
            table.destination_schema or table.schema_name,
            table.destination_table or table.table_name,
        )


async def _finish(
    session: AsyncSession,
    operation: PipelineOperation,
    table: PipelineTable,
    steps: list[str],
) -> None:
    for step in steps:
        if step in {"wait_snapshot", "verify_destination", "complete"} and not _completed(
            operation, step
        ):
            await _checkpoint(session, operation, step, steps)
    operation.status = "SUCCEEDED"
    operation.current_step = "complete"
    operation.progress = 100
    operation.finished_at = now()
    table.cdc_status = "STREAMING" if operation.type != "REMOVE_TABLE" else table.cdc_status
    if table.destination_status not in {"FAILED", "DEGRADED"}:
        table.destination_status = "HEALTHY"
    audit(session, operation.metadata_json.get("actor", "worker"), "operation.succeeded", operation)


async def sync_snapshot_operations(session: AsyncSession) -> int:
    operations = (
        await session.scalars(
            select(PipelineOperation)
            .where(
                PipelineOperation.status == "WAITING",
                PipelineOperation.current_step == "wait_snapshot",
            )
            .limit(100)
        )
    ).all()
    updated = 0
    for operation in operations:
        if operation.table_id is None:
            continue
        pipeline = await get(session, Pipeline, operation.pipeline_id)
        table = await get(session, PipelineTable, operation.table_id)
        kafka = await get(session, KafkaCluster, pipeline.kafka_cluster_id)
        topic = operation.metadata_json.get("notification_topic")
        signal_id = operation.metadata_json.get("signal_id")
        if not topic or not signal_id:
            continue
        try:
            records = await KafkaExplorer().notifications(kafka, topic)
        except DomainError:
            continue
        matching = [record for record in records if record.get("id") == signal_id]
        for record in matching:
            event = record.get("type")
            details = record.get("additional_data") or {}
            table.snapshot_last_activity_at = now()
            if event == "IN_PROGRESS":
                table.snapshot_current_chunk = details.get("last_processed_key")
            elif event == "TABLE_SCAN_COMPLETED":
                result = details.get("status")
                if result in {"SUCCEEDED", "EMPTY"}:
                    rows = details.get("total_rows_scanned")
                    table.snapshot_rows_processed = int(str(rows)) if str(rows).isdigit() else None
                    table.snapshot_status = "COMPLETED"
                    table.snapshot_finished_at = now()
                else:
                    table.snapshot_status = "FAILED"
                    operation.status = "FAILED"
                    operation.error_code = f"SNAPSHOT_{result or 'FAILED'}"
                    operation.error_message = "Debezium could not complete the table snapshot"
                    operation.finished_at = now()
            elif event == "ABORTED":
                table.snapshot_status = "CANCELLED"
                operation.status = "CANCELLED"
                operation.finished_at = now()
            elif event == "COMPLETED" and table.snapshot_status == "RUNNING":
                table.snapshot_status = "COMPLETED"
                table.snapshot_finished_at = now()
        if table.snapshot_status == "COMPLETED" and operation.status == "WAITING":
            steps = operation.metadata_json.get("steps", RESYNC_STEPS)
            await _finish(session, operation, table, steps)
            updated += 1
    return updated
