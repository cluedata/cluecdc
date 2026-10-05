import uuid
from collections.abc import Sequence
from typing import Literal, cast

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.connect import KafkaConnectClient
from app.adapters.kafka import KafkaExplorer
from app.alerts.monitoring import observe_connector
from app.core.errors import DomainError, redact
from app.models.entities import (
    ConnectCluster,
    Connector,
    KafkaCluster,
    Pipeline,
    PipelineDestination,
    PipelineEvent,
    PipelineTable,
    Source,
    SourceTable,
    now,
)
from app.providers import get_provider
from app.providers.server_ids import assign_mysql_connector_server_id
from app.repositories.metadata import audit, get, serialize
from app.schemas.requests import PipelineInput, TableSelection
from app.services.debezium import derive_actual_state, notification_topic
from app.services.source import adapter as source_adapter

log = structlog.get_logger()


def aggregate_pipeline_state(capture_state: str, delivery_states: list[str]) -> str:
    """A healthy source path remains available when a downstream sink is degraded."""
    if capture_state != "RUNNING":
        return capture_state
    if any(state != "RUNNING" for state in delivery_states):
        return "DEGRADED"
    return "RUNNING"


async def build(session: AsyncSession, data: PipelineInput) -> tuple[dict, ConnectCluster]:
    source = await get(session, Source, data.source_id)
    if source.category != "DATABASE" or "SOURCE" not in source.capabilities_json:
        raise DomainError("SOURCE_CONNECTION_REQUIRED", "Select a database source connection", 422)
    cluster = await get(session, ConnectCluster, data.connect_cluster_id)
    kafka = await get(session, KafkaCluster, data.kafka_cluster_id)
    if cluster.kafka_cluster_id != data.kafka_cluster_id:
        raise DomainError(
            "CLUSTER_MISMATCH", "Connect cluster must belong to the selected Kafka cluster"
        )
    discovered = {
        (t.schema_name, t.table_name): t
        for t in (
            await session.scalars(select(SourceTable).where(SourceTable.source_id == source.id))
        ).all()
    }
    for table in data.tables:
        found = discovered.get((table.schema_name, table.table_name))
        if not found:
            raise DomainError(
                "TABLE_NOT_DISCOVERED", "Discover the source before selecting tables", 422
            )
        if not found.cdc_ready:
            raise DomainError(
                "TABLE_NOT_READY",
                "Selected table has unresolved CDC issues",
                422,
                {"table": f"{table.schema_name}.{table.table_name}", "issues": found.cdc_issues},
            )
    # Connect persists only this reference. Its ConfigProvider resolves the encrypted value.
    config = get_provider(source.type).build_source_config(
        source,
        data,
        f"${{cluecdc:{source.secret_ref}:password}}",
        f"cluecdc-preview-{data.topic_prefix}",
        kafka.bootstrap_servers,
    )
    return config, cluster


async def preview(session: AsyncSession, data: PipelineInput) -> dict:
    config, _ = await build(session, data)
    return {
        "config": redact(config),
        "topics": [f"{data.topic_prefix}.{t.schema_name}.{t.table_name}" for t in data.tables],
    }


async def validate_tables_live(
    session: AsyncSession, data: PipelineInput
) -> dict[tuple[str, str], dict]:
    source = await get(session, Source, data.source_id)
    database = await source_adapter(session, source)
    await session.commit()
    results = {}
    for table in data.tables:
        readiness = await database.inspect_table(table.schema_name, table.table_name)
        if not readiness["ready"]:
            raise DomainError(
                "TABLE_NOT_READY",
                f"{table.schema_name}.{table.table_name} is not ready for CDC",
                422,
                readiness,
            )
        results[(table.schema_name, table.table_name)] = readiness
    return results


async def create(session: AsyncSession, data: PipelineInput, actor: str) -> Pipeline:
    config, cluster = await build(session, data)
    readiness = await validate_tables_live(session, data)
    await KafkaConnectClient(cluster.base_url).validate(config)
    pipeline_id = uuid.uuid4()
    config_options = data.model_dump(
        mode="json",
        exclude={
            "name",
            "source_id",
            "kafka_cluster_id",
            "connect_cluster_id",
            "topic_prefix",
            "snapshot_mode",
            "tables",
        },
    )
    source = await get(session, Source, data.source_id)
    if source.type == "mysql":
        options = dict(config_options.get("provider_options", {}))
        options["server_id"] = await assign_mysql_connector_server_id(session, source, pipeline_id)
        config_options["provider_options"] = options
    pipeline = Pipeline(
        id=pipeline_id,
        name=data.name,
        source_id=data.source_id,
        kafka_cluster_id=data.kafka_cluster_id,
        connect_cluster_id=data.connect_cluster_id,
        topic_prefix=data.topic_prefix,
        snapshot_mode=data.snapshot_mode,
        config_options=config_options,
    )
    session.add(pipeline)
    await session.flush()
    for t in data.tables:
        current = readiness[(t.schema_name, t.table_name)]
        session.add(
            PipelineTable(
                pipeline_id=pipeline.id,
                schema_name=t.schema_name,
                table_name=t.table_name,
                topic_name=f"{data.topic_prefix}.{t.schema_name}.{t.table_name}",
                primary_key_columns=current["primary_key_columns"],
                destination_schema=t.schema_name,
                destination_table=t.table_name,
                snapshot_status="NOT_REQUIRED" if data.snapshot_mode == "no_data" else "PENDING",
                snapshot_estimated_rows=current["estimated_rows"],
            )
        )
    audit(session, actor, "pipeline.created", pipeline)
    return pipeline


async def input_for(session: AsyncSession, pipeline: Pipeline) -> PipelineInput:
    tables = (
        await session.scalars(
            select(PipelineTable).where(
                PipelineTable.pipeline_id == pipeline.id,
                PipelineTable.cdc_status.not_in(["REMOVING", "REMOVED"]),
            )
        )
    ).all()
    return PipelineInput(
        name=pipeline.name,
        source_id=pipeline.source_id,
        kafka_cluster_id=pipeline.kafka_cluster_id,
        connect_cluster_id=pipeline.connect_cluster_id,
        topic_prefix=pipeline.topic_prefix,
        snapshot_mode=cast(
            Literal["initial", "no_data", "never", "always"], pipeline.snapshot_mode
        ),
        tables=[TableSelection(schema_name=t.schema_name, table_name=t.table_name) for t in tables],
        **pipeline.config_options,
    )


async def locked(session: AsyncSession, pipeline_id: uuid.UUID) -> Pipeline:
    entity = await session.scalar(
        select(Pipeline).where(Pipeline.id == pipeline_id).with_for_update()
    )
    if entity is None:
        raise DomainError("NOT_FOUND", "Pipeline was not found", 404)
    return entity


async def prepare_topics(session: AsyncSession, pipeline: Pipeline, actor: str) -> dict:
    tables = (
        await session.scalars(select(PipelineTable).where(PipelineTable.pipeline_id == pipeline.id))
    ).all()
    cluster = await get(session, KafkaCluster, pipeline.kafka_cluster_id)
    await session.commit()
    result = await KafkaExplorer().ensure_topics(cluster, [table.topic_name for table in tables])
    cluster.status = "HEALTHY"
    audit(session, actor, "pipeline.topics_prepared", pipeline)
    return result


async def deploy(session: AsyncSession, pipeline_id: uuid.UUID, actor: str) -> dict:
    structlog.contextvars.bind_contextvars(pipeline_id=str(pipeline_id))
    pipeline = await get(session, Pipeline, pipeline_id)
    if pipeline.connector_id:
        raise DomainError(
            "ALREADY_DEPLOYED", "Pipeline already has a connector; use resume or restart", 409
        )
    data = await input_for(session, pipeline)
    config, cluster = await build(session, data)
    source = await get(session, Source, pipeline.source_id)
    kafka = await get(session, KafkaCluster, pipeline.kafka_cluster_id)
    name = f"cluecdc-source-{pipeline.id.hex}"
    config = get_provider(source.type).build_source_config(
        source,
        data,
        f"${{cluecdc:{source.secret_ref}:password}}",
        name,
        kafka.bootstrap_servers,
    )
    client = KafkaConnectClient(cluster.base_url)
    # Close the metadata transaction before any Kafka/Connect network calls.
    await session.commit()
    await client.validate(config)
    await KafkaExplorer().ensure_topics(kafka, [notification_topic(pipeline.topic_prefix)])
    await prepare_topics(session, pipeline, actor)
    await session.commit()
    # POST preserves externally owned connectors; never overwrite on collision.
    await client.create(name, config)
    try:
        pipeline = await locked(session, pipeline_id)
        if pipeline.connector_id:
            raise DomainError("ALREADY_DEPLOYED", "Another request deployed this pipeline", 409)
        connector = Connector(
            name=name,
            connect_cluster_id=cluster.id,
            connector_class=config["connector.class"],
            config_json=redact(config),
            desired_state="RUNNING",
            actual_state="UNKNOWN",
        )
        session.add(connector)
        await session.flush()
        pipeline.connector_id = connector.id
        pipeline.desired_state = "RUNNING"
        pipeline.actual_state = "UNKNOWN"
        audit(session, actor, "pipeline.deployed", pipeline)
        await session.commit()
    except Exception:
        await session.rollback()
        try:
            await client.operate(name, "delete")
        except DomainError:
            log.error(
                "deployment_compensation_failed", connector_name=name, pipeline_id=str(pipeline_id)
            )
        raise
    log.info(
        "pipeline_operation",
        pipeline_id=str(pipeline_id),
        connector_name=name,
        connect_cluster=str(cluster.id),
        operation="deploy",
        result="success",
    )
    return serialize(pipeline)


async def reconcile(session: AsyncSession, pipeline: Pipeline) -> dict:
    structlog.contextvars.bind_contextvars(pipeline_id=str(pipeline.id))
    if not pipeline.connector_id:
        return {"actual_state": "UNKNOWN", "connector": None, "tasks": []}
    connector = await get(session, Connector, pipeline.connector_id)
    cluster = await get(session, ConnectCluster, pipeline.connect_cluster_id)
    previous = pipeline.actual_state
    await session.commit()
    try:
        status = await KafkaConnectClient(cluster.base_url).status(connector.name)
        capture_state = derive_actual_state(status)
        cluster.status = "HEALTHY"
    except DomainError as exc:
        status = {"error": {"code": exc.code, "message": exc.message}, "tasks": []}
        capture_state = "UNKNOWN"
        cluster.status = "UNAVAILABLE"
    links = (
        await session.scalars(
            select(PipelineDestination).where(PipelineDestination.pipeline_id == pipeline.id)
        )
    ).all()
    state = aggregate_pipeline_state(capture_state, [link.actual_state for link in links])
    connector.actual_state = capture_state
    pipeline.actual_state = state
    connector.runtime_json = status
    tables = (
        await session.scalars(
            select(PipelineTable).where(
                PipelineTable.pipeline_id == pipeline.id,
                PipelineTable.cdc_status != "REMOVED",
            )
        )
    ).all()
    for table in tables:
        if capture_state == "RUNNING" and table.cdc_status not in {
            "PAUSED",
            "REMOVING",
            "REMOVED",
        }:
            table.cdc_status = "STREAMING"
        elif capture_state in {"FAILED", "UNKNOWN"}:
            table.cdc_status = "FAILED"
        mapped_states = [
            link.actual_state
            for link in links
            if any(mapping.get("topic") == table.topic_name for mapping in link.topic_mapping_json)
        ]
        if not mapped_states:
            table.destination_status = "READY"
        elif all(mapped == "RUNNING" for mapped in mapped_states):
            table.destination_status = (
                "CATCHING_UP" if table.snapshot_status == "RUNNING" else "HEALTHY"
            )
        elif any(mapped == "FAILED" for mapped in mapped_states):
            table.destination_status = "FAILED"
        else:
            table.destination_status = "DEGRADED"
    if capture_state == "RUNNING" and any(
        table.snapshot_status in {"PENDING", "RUNNING"} for table in tables
    ):
        kafka = await get(session, KafkaCluster, pipeline.kafka_cluster_id)
        await session.commit()
        try:
            notifications = await KafkaExplorer().notifications(
                kafka, notification_topic(pipeline.topic_prefix)
            )
            _apply_initial_snapshot_notifications(tables, notifications)
        except DomainError:
            pass
    if state in {"FAILED", "DEGRADED", "UNKNOWN"} and state != previous:
        session.add(
            PipelineEvent(
                pipeline_id=pipeline.id,
                connector_name=connector.name,
                category="CONNECT",
                severity="error" if state != "UNKNOWN" else "warning",
                message=f"Connector runtime state changed to {state}",
            )
        )
    if state == "RUNNING" and previous != "RUNNING":
        errors = (
            await session.scalars(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_id == pipeline.id,
                    PipelineEvent.category == "CONNECT",
                    PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]),
                )
            )
        ).all()
        for error in errors:
            error.status = "RESOLVED"
    if state != previous:
        audit(session, "reconciler", "pipeline.state_observed", pipeline)
    await observe_connector(
        session,
        pipeline=pipeline,
        connector=connector,
        status=status,
        state=state,
        desired_state=pipeline.desired_state,
    )
    return {"actual_state": state, **status}


def _apply_initial_snapshot_notifications(
    tables: Sequence[PipelineTable], notifications: list[dict]
) -> None:
    initial = [
        record for record in notifications if record.get("aggregate_type") == "Initial Snapshot"
    ]
    for record in initial:
        event = record.get("type")
        details = record.get("additional_data") or {}
        scanned = details.get("scanned_collection")
        if event in {"STARTED", "IN_PROGRESS"}:
            current = details.get("current_collection_in_progress")
            for table in tables:
                if not current or current.endswith(f"{table.schema_name}.{table.table_name}"):
                    table.snapshot_status = "RUNNING"
                    table.snapshot_last_activity_at = now()
        elif event == "TABLE_SCAN_COMPLETED" and scanned:
            for table in tables:
                if scanned.endswith(f"{table.schema_name}.{table.table_name}"):
                    table.snapshot_status = (
                        "COMPLETED" if details.get("status") in {"SUCCEEDED", "EMPTY"} else "FAILED"
                    )
                    rows = details.get("total_rows_scanned")
                    table.snapshot_rows_processed = int(str(rows)) if str(rows).isdigit() else None
                    table.snapshot_finished_at = now()
        elif event == "COMPLETED":
            for table in tables:
                if table.snapshot_status in {"PENDING", "RUNNING"}:
                    table.snapshot_status = "COMPLETED"
                    table.snapshot_finished_at = now()
        elif event == "SKIPPED":
            # Debezium emits SKIPPED after finding an existing offset that
            # records a completed initial snapshot. This is the only reliable
            # backward-compatible completion signal for pipelines deployed
            # before ClueCDC persisted per-table snapshot state.
            for table in tables:
                if table.snapshot_status == "PENDING":
                    table.snapshot_status = "COMPLETED"
                    table.snapshot_finished_at = now()


async def operate(
    session: AsyncSession,
    pipeline_id: uuid.UUID,
    operation: str,
    actor: str,
    task: int | None = None,
) -> dict:
    structlog.contextvars.bind_contextvars(pipeline_id=str(pipeline_id))
    pipeline = await get(session, Pipeline, pipeline_id)
    if operation == "delete" and await session.scalar(
        select(PipelineDestination.id)
        .where(PipelineDestination.pipeline_id == pipeline_id)
        .limit(1)
    ):
        raise DomainError(
            "PIPELINE_IN_USE",
            "Remove downstream deliveries before deleting this capture pipeline",
            409,
        )
    if not pipeline.connector_id:
        if operation == "delete":
            audit(session, actor, "pipeline.deleted", pipeline)
            await session.delete(pipeline)
            return {"deleted": True}
        raise DomainError("NOT_DEPLOYED", "Deploy the pipeline first", 409)
    connector = await get(session, Connector, pipeline.connector_id)
    cluster = await get(session, ConnectCluster, pipeline.connect_cluster_id)
    client = KafkaConnectClient(cluster.base_url)
    connector_name = connector.name
    connector_id = connector.id
    await session.commit()
    if operation == "delete":
        try:
            await client.operate(connector_name, "delete")
        except DomainError as exc:
            if exc.code != "CONNECTOR_NOT_FOUND":
                raise
        pipeline = await locked(session, pipeline_id)
        connector = await get(session, Connector, connector_id)
        for incident in (
            await session.scalars(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_id == pipeline.id,
                    PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]),
                )
            )
        ).all():
            incident.status = "RESOLVED"
        audit(session, actor, "pipeline.deleted", pipeline)
        await session.delete(pipeline)
        await session.flush()
        await session.delete(connector)
        return {"deleted": True}
    await client.operate(connector_name, operation, task)
    pipeline = await locked(session, pipeline_id)
    connector = await get(session, Connector, connector_id)
    if operation in {"pause", "resume"}:
        pipeline.desired_state = connector.desired_state = (
            "PAUSED" if operation == "pause" else "RUNNING"
        )
    action = {
        "pause": "paused",
        "resume": "resumed",
        "restart": "restarted",
        "restart-task": "task_restarted",
    }[operation]
    audit(session, actor, f"pipeline.{action}", pipeline)
    await session.commit()
    pipeline = await get(session, Pipeline, pipeline_id)
    status = await reconcile(session, pipeline)
    return {"desired_state": pipeline.desired_state, **status}
