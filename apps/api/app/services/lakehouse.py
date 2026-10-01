import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.connect import KafkaConnectClient
from app.alerts.monitoring import observe_connector
from app.connection_providers import get_connection_provider
from app.connection_providers.base import secret_reference
from app.core.errors import DomainError, redact
from app.models.entities import (
    ConnectCluster,
    Connection,
    Connector,
    LakehouseTarget,
    Pipeline,
    PipelineDestination,
    PipelineEvent,
    PipelineTable,
    SourceTable,
)
from app.repositories.metadata import audit, get, serialize
from app.schemas.lakehouse import IcebergDeliveryInput, LakehouseTargetInput
from app.services import connections as connection_service
from app.services.iceberg_types import map_columns

ICEBERG_SINK_CLASS = "org.apache.iceberg.connect.IcebergSinkConnector"
DEBEZIUM_TRANSFORM_CLASS = "org.apache.iceberg.connect.transforms.DebeziumTransform"
LEGACY_ICEBERG_SINK_CLASS = "io.tabular.iceberg.connect.IcebergSinkConnector"


async def _typed_connection(
    session: AsyncSession, identifier: uuid.UUID, category: str
) -> Connection:
    connection = await get(session, Connection, identifier)
    if connection.category != category:
        raise DomainError(
            "CONNECTION_CATEGORY_MISMATCH",
            f"Expected a {category.lower().replace('_', ' ')} connection",
            422,
        )
    return connection


async def validate_references(session: AsyncSession, data: LakehouseTargetInput) -> Connection:
    storage = await _typed_connection(session, data.storage_connection_id, "OBJECT_STORAGE")
    if storage.provider not in {"AWS_S3", "MINIO"}:
        raise DomainError(
            "OBJECT_STORAGE_PROVIDER_UNSUPPORTED",
            "Lakehouse targets currently support only Amazon S3 and MinIO",
            422,
        )
    bucket = storage.config_json.get("bucket")
    if data.warehouse.startswith("s3://") and bucket:
        warehouse_bucket = data.warehouse[5:].split("/", 1)[0]
        if warehouse_bucket != bucket:
            raise DomainError(
                "WAREHOUSE_BUCKET_MISMATCH",
                "Lakehouse warehouse must use the selected storage connection bucket",
                422,
            )
    return storage


async def create(session: AsyncSession, data: LakehouseTargetInput, actor: str) -> LakehouseTarget:
    await validate_references(session, data)
    destination = LakehouseTarget(**data.model_dump())
    session.add(destination)
    await session.flush()
    audit(session, actor, "lakehouse_destination.created", destination)
    return destination


async def update(
    session: AsyncSession,
    destination: LakehouseTarget,
    data: LakehouseTargetInput,
    actor: str,
) -> LakehouseTarget:
    if await session.scalar(
        select(PipelineDestination.id)
        .where(PipelineDestination.lakehouse_destination_id == destination.id)
        .limit(1)
    ):
        raise DomainError(
            "LAKEHOUSE_DESTINATION_IN_USE",
            "Remove Lakehouse deliveries before changing this destination",
            409,
        )
    await validate_references(session, data)
    before = serialize(destination)
    for key, value in data.model_dump().items():
        setattr(destination, key, value)
    destination.status = "UNKNOWN"
    audit(session, actor, "lakehouse_destination.updated", destination, before)
    return destination


async def detail(session: AsyncSession, destination: LakehouseTarget) -> dict:
    result = serialize(destination)
    # Retain legacy columns in storage for rollback compatibility, but do not expose
    # removed catalog/query-engine concepts in the public storage-only contract.
    result.pop("catalog_connection_id", None)
    result.pop("query_engine_connection_id", None)
    result["storage_connection"] = connection_service.view(
        await get(session, Connection, destination.storage_connection_id)
    )
    result["delivery_count"] = await session.scalar(
        select(__import__("sqlalchemy").func.count())
        .select_from(PipelineDestination)
        .where(PipelineDestination.lakehouse_destination_id == destination.id)
    )
    return result


async def delete(session: AsyncSession, destination: LakehouseTarget, actor: str) -> dict:
    if await session.scalar(
        select(PipelineDestination.id)
        .where(PipelineDestination.lakehouse_destination_id == destination.id)
        .limit(1)
    ):
        raise DomainError("LAKEHOUSE_DESTINATION_IN_USE", "Remove deliveries before deleting", 409)
    audit(session, actor, "lakehouse_destination.deleted", destination)
    await session.delete(destination)
    return {"deleted": True}


def _secret_properties(
    connection: Connection, prefix: str, configured_keys: set[str]
) -> dict[str, str]:
    result = {}
    if "access_key" in configured_keys:
        result[prefix + "s3.access-key-id"] = secret_reference(connection, "access_key")
    if "secret_key" in configured_keys:
        result[prefix + "s3.secret-access-key"] = secret_reference(connection, "secret_key")
    if "session_token" in configured_keys:
        result[prefix + "s3.session-token"] = secret_reference(connection, "session_token")
    return result


async def build_connector_config(
    session: AsyncSession,
    destination: LakehouseTarget,
    data: IcebergDeliveryInput,
    connector_name: str,
) -> tuple[dict, ConnectCluster, list[dict]]:
    pipeline = await get(session, Pipeline, data.pipeline_id)
    if not pipeline.connector_id:
        raise DomainError(
            "PIPELINE_NOT_DEPLOYED", "Deploy the capture pipeline before adding delivery", 422
        )
    cluster = await get(
        session, ConnectCluster, data.connect_cluster_id or pipeline.connect_cluster_id
    )
    if cluster.kafka_cluster_id != pipeline.kafka_cluster_id:
        raise DomainError("CLUSTER_MISMATCH", "Connect and pipeline Kafka clusters differ", 422)
    client = KafkaConnectClient(cluster.base_url)
    plugins = await client.plugins()
    installed_class = next(
        (
            connector_class
            for connector_class in (ICEBERG_SINK_CLASS, LEGACY_ICEBERG_SINK_CLASS)
            if any(item.get("class") == connector_class for item in plugins)
        ),
        None,
    )
    if not installed_class:
        raise DomainError(
            "ICEBERG_SINK_PLUGIN_MISSING",
            "Apache Iceberg sink plugin is not installed on the Kafka Connect cluster",
            422,
            {"connector_class": ICEBERG_SINK_CLASS},
        )
    tables = (
        await session.scalars(
            select(PipelineTable).where(
                PipelineTable.pipeline_id == pipeline.id,
                PipelineTable.removed_at.is_(None),
            )
        )
    ).all()
    if not tables:
        raise DomainError("PIPELINE_TABLES_REQUIRED", "Pipeline has no active tables", 422)
    source_tables = {
        (item.schema_name, item.table_name): item
        for item in (
            await session.scalars(
                select(SourceTable).where(SourceTable.source_id == pipeline.source_id)
            )
        ).all()
    }
    table_metadata = []
    targets: list[str] = []
    target_routes: dict[str, str] = {}
    for table in tables:
        source = source_tables.get((table.schema_name, table.table_name))
        if not source:
            raise DomainError(
                "SOURCE_SCHEMA_REQUIRED", "Run source discovery before deploying Iceberg", 422
            )
        columns = map_columns(source.columns_json)
        target_name = data.table_routing.get(table.topic_name, table.table_name)
        qualified = f"{destination.namespace}.{target_name}"
        if qualified in targets:
            raise DomainError(
                "DUPLICATE_TABLE_ROUTE", "Multiple topics route to one Iceberg table", 422
            )
        identifiers = destination.identifier_fields or source.primary_key_columns
        if destination.write_mode == "UPSERT" and not identifiers:
            raise DomainError(
                "ICEBERG_IDENTIFIER_REQUIRED", "UPSERT delivery requires identifier fields", 422
            )
        column_names = {column["name"] for column in columns}
        if set(identifiers) - column_names:
            raise DomainError(
                "ICEBERG_IDENTIFIER_INVALID",
                "Identifier fields must exist in every source table",
                422,
                {"table": table.table_name, "missing": sorted(set(identifiers) - column_names)},
            )
        targets.append(qualified)
        target_routes[qualified] = re.escape(qualified)
        table_metadata.append(
            {
                "topic": table.topic_name,
                "source": f"{table.schema_name}.{table.table_name}",
                "target": qualified,
                "identifier_fields": identifiers,
                "columns": columns,
            }
        )
    storage = await get(session, Connection, destination.storage_connection_id)
    storage_credentials = await connection_service.credentials(session, storage)
    storage_provider = get_connection_provider(storage, storage_credentials)
    config = {
        "name": connector_name,
        "connector.class": installed_class,
        "tasks.max": str(data.tasks_max),
        "topics": ",".join(item.topic_name for item in tables),
        "iceberg.tables": ",".join(targets),
        "iceberg.tables.route-field": "_cdc.target",
        "iceberg.tables.auto-create-enabled": str(destination.auto_create_tables).lower(),
        "iceberg.tables.evolve-schema-enabled": str(destination.schema_evolution).lower(),
        "iceberg.tables.schema-force-optional": "false",
        "iceberg.tables.schema-case-insensitive": "false",
        "iceberg.tables.auto-create-props.format-version": "2",
        "iceberg.tables.auto-create-props.write.format.default": destination.file_format.lower(),
        "iceberg.control.commit.interval-ms": str(data.commit_interval_ms),
        "iceberg.catalog.type": "hadoop",
        "iceberg.catalog.warehouse": destination.warehouse,
        "consumer.override.auto.offset.reset": "earliest",
        "consumer.override.group.id": connector_name,
        "key.converter": "org.apache.kafka.connect.json.JsonConverter",
        "key.converter.schemas.enable": "false",
        "value.converter": "org.apache.kafka.connect.json.JsonConverter",
        "value.converter.schemas.enable": "false",
        "transforms": "debezium",
        "transforms.debezium.type": (
            DEBEZIUM_TRANSFORM_CLASS
            if installed_class == ICEBERG_SINK_CLASS
            else "io.tabular.iceberg.connect.transforms.DebeziumTransform"
        ),
        "transforms.debezium.cdc.target.pattern": f"{destination.namespace}.{{table}}",
        "errors.tolerance": "none",
        **storage_provider.iceberg_properties(),
        **_secret_properties(storage, "iceberg.catalog.", set(storage_credentials)),
    }
    if destination.write_mode == "UPSERT":
        config["iceberg.tables.cdc-field"] = "_cdc.op"
        config["iceberg.tables.upsert-mode-enabled"] = "true"
    for item in table_metadata:
        table_key = str(item["target"])
        config[f"iceberg.table.{table_key}.route-regex"] = target_routes[table_key]
        config[f"iceberg.table.{table_key}.id-columns"] = ",".join(
            str(value) for value in item["identifier_fields"]
        )
        if destination.partition_config:
            config[f"iceberg.table.{table_key}.partition-by"] = ",".join(
                destination.partition_config
            )
    await client.validate(config)
    return config, cluster, table_metadata


async def preview_delivery(
    session: AsyncSession, destination: LakehouseTarget, data: IcebergDeliveryInput
) -> dict:
    config, cluster, metadata = await build_connector_config(
        session, destination, data, "cluecdc-iceberg-preview"
    )
    return {
        "config": redact(config),
        "connect_cluster_id": cluster.id,
        "connector_class": config["connector.class"],
        "tables": metadata,
    }


async def deploy(
    session: AsyncSession,
    destination: LakehouseTarget,
    data: IcebergDeliveryInput,
    actor: str,
) -> dict:
    identifier = uuid.uuid4()
    connector_name = "cluecdc-iceberg-" + identifier.hex
    config, cluster, metadata = await build_connector_config(
        session, destination, data, connector_name
    )
    client = KafkaConnectClient(cluster.base_url)
    await client.create(connector_name, config)
    try:
        connector = Connector(
            name=connector_name,
            connector_type="sink",
            connect_cluster_id=cluster.id,
            connector_class=config["connector.class"],
            config_json=redact(config),
            desired_state="RUNNING",
            actual_state="UNKNOWN",
        )
        session.add(connector)
        await session.flush()
        link = PipelineDestination(
            id=identifier,
            pipeline_id=data.pipeline_id,
            destination_id=None,
            lakehouse_destination_id=destination.id,
            delivery_type="ICEBERG",
            connector_id=connector.id,
            name=data.name,
            delivery_mode=destination.write_mode.lower(),
            topic_mapping_json=[
                {
                    "topic": item["topic"],
                    "schema_name": destination.namespace,
                    "table_name": str(item["target"]).split(".", 1)[-1],
                }
                for item in metadata
            ],
            configuration_json=data.model_dump(mode="json"),
            desired_state="RUNNING",
            actual_state="UNKNOWN",
        )
        session.add(link)
        destination.status = "HEALTHY"
        await session.flush()
        audit(session, actor, "lakehouse_delivery.deployed", link)
        await session.commit()
    except Exception:
        await session.rollback()
        try:
            await client.operate(connector_name, "delete")
        except DomainError:
            pass
        raise
    return await delivery_view(session, link)


async def delivery_view(session: AsyncSession, link: PipelineDestination) -> dict:
    if not link.lakehouse_destination_id:
        raise DomainError("INVALID_DELIVERY", "Lakehouse delivery is missing its destination", 500)
    result = serialize(link)
    result["destination"] = await detail(
        session, await get(session, LakehouseTarget, link.lakehouse_destination_id)
    )
    result["pipeline"] = serialize(await get(session, Pipeline, link.pipeline_id))
    result["connector"] = (
        serialize(await get(session, Connector, link.connector_id)) if link.connector_id else None
    )
    return result


def _runtime_state(status: dict) -> str:
    connector = status.get("connector", {}).get("state", "UNKNOWN")
    tasks = [item.get("state", "UNKNOWN") for item in status.get("tasks", [])]
    if connector == "FAILED" or any(state == "FAILED" for state in tasks):
        return "FAILED" if connector == "FAILED" else "DEGRADED"
    if connector == "RUNNING" and all(state == "RUNNING" for state in tasks):
        return "RUNNING"
    return connector


async def reconcile(session: AsyncSession, link: PipelineDestination) -> dict:
    if not link.connector_id:
        return {"actual_state": "UNKNOWN", "tasks": []}
    connector = await get(session, Connector, link.connector_id)
    pipeline = await get(session, Pipeline, link.pipeline_id)
    cluster = await get(session, ConnectCluster, connector.connect_cluster_id)
    previous = link.actual_state
    try:
        status = await KafkaConnectClient(cluster.base_url).status(connector.name)
        state = _runtime_state(status)
        cluster.status = "HEALTHY"
    except DomainError as error:
        status = {"error": {"code": error.code, "message": error.message}, "tasks": []}
        state = "UNKNOWN"
        cluster.status = "ERROR"
    if previous != state:
        link.actual_state = connector.actual_state = state
        audit(session, "reconciler", "lakehouse_delivery.state_observed", link)
        if state in {"FAILED", "DEGRADED", "UNKNOWN"}:
            error_info = status.get("error") or {}
            failed_tasks = [
                {
                    "id": task.get("id"),
                    "state": task.get("state"),
                    "error": task.get("error"),
                }
                for task in status.get("tasks", [])
                if task.get("state") == "FAILED"
            ]
            session.add(
                PipelineEvent(
                    pipeline_id=link.pipeline_id,
                    delivery_id=link.id,
                    lakehouse_destination_id=link.lakehouse_destination_id,
                    connector_id=connector.id,
                    connector_name=connector.name,
                    category="LAKEHOUSE_DELIVERY",
                    component="iceberg-delivery",
                    error_code=error_info.get("code") or "ICEBERG_DELIVERY_FAILED",
                    severity="error" if state == "FAILED" else "warning",
                    message=error_info.get("message")
                    or f"Iceberg delivery state changed to {state}",
                    technical_details={"state": state, "failed_tasks": failed_tasks},
                    recoverable=True,
                )
            )
        elif state == "RUNNING":
            events = (
                await session.scalars(
                    select(PipelineEvent).where(
                        PipelineEvent.delivery_id == link.id,
                        PipelineEvent.category == "LAKEHOUSE_DELIVERY",
                        PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]),
                    )
                )
            ).all()
            for event in events:
                event.status = "RESOLVED"
    connector.runtime_json = status
    await observe_connector(
        session,
        pipeline=pipeline,
        connector=connector,
        status=status,
        state=state,
        desired_state=link.desired_state,
        delivery=link,
    )
    return {
        "delivery_id": link.id,
        "desired_state": link.desired_state,
        "actual_state": state,
        **status,
    }


async def operate(
    session: AsyncSession,
    link: PipelineDestination,
    operation: str,
    actor: str,
    task: int | None = None,
) -> dict:
    if not link.connector_id:
        raise DomainError("NOT_DEPLOYED", "Delivery has no connector", 409)
    connector = await get(session, Connector, link.connector_id)
    cluster = await get(session, ConnectCluster, connector.connect_cluster_id)
    try:
        await KafkaConnectClient(cluster.base_url).operate(connector.name, operation, task)
    except DomainError as error:
        if operation != "delete" or error.code != "CONNECTOR_NOT_FOUND":
            raise
    if operation == "delete":
        audit(session, actor, "lakehouse_delivery.deleted", link)
        await session.delete(link)
        await session.flush()
        await session.delete(connector)
        await session.commit()
        return {"deleted": True}
    if operation in {"pause", "resume"}:
        desired = "PAUSED" if operation == "pause" else "RUNNING"
        link.desired_state = connector.desired_state = desired
    audit(session, actor, f"lakehouse_delivery.{operation}", link)
    await session.commit()
    return {"delivery_id": link.id, "desired_state": link.desired_state}
