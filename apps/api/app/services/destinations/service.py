import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.connect import KafkaConnectClient
from app.adapters.kafka import KafkaExplorer
from app.alerts.monitoring import observe_connector, observe_database_connection
from app.core.errors import DomainError, redact
from app.models.entities import (
    ConnectCluster,
    Connection,
    Connector,
    Destination,
    KafkaCluster,
    Pipeline,
    PipelineDestination,
    PipelineEvent,
    PipelineTable,
    SourceTable,
    now,
)
from app.providers import get_provider
from app.providers.types import analyze_columns
from app.repositories.metadata import audit, get, serialize
from app.schemas.connections import ConnectionInput
from app.schemas.requests import DeliveryInput, DestinationInput
from app.services import connections as connection_service
from app.services.debezium import derive_actual_state
from app.services.destinations.adapter import DestinationAdapter
from app.services.destinations.providers import delivery_provider
from app.services.secrets import secret_provider

log = structlog.get_logger()


async def locked(session: AsyncSession, identifier: uuid.UUID) -> Destination:
    destination = await session.scalar(
        select(Connection).where(Connection.id == identifier).with_for_update()
    )
    if destination is None or "DESTINATION" not in destination.capabilities_json:
        raise DomainError("NOT_FOUND", "Destination was not found", 404)
    return destination


async def create(session: AsyncSession, data: DestinationInput, actor: str) -> Destination:
    if not data.password or not data.password.get_secret_value():
        raise DomainError("PASSWORD_REQUIRED", "A destination password is required", 422)
    return await connection_service.create(
        session,
        ConnectionInput(
            name=data.name,
            description=data.description,
            category="DATABASE",
            provider=data.type.upper(),
            config=data.model_dump(exclude={"password", "name", "type", "description"}),
            credentials={"password": data.password},
            capabilities=["DESTINATION"],
        ),
        actor,
    )


async def deliveries(session: AsyncSession, identifier: uuid.UUID) -> list[PipelineDestination]:
    return list(
        (
            await session.scalars(
                select(PipelineDestination)
                .where(PipelineDestination.destination_id == identifier)
                .order_by(PipelineDestination.created_at)
            )
        ).all()
    )


async def all_deliveries(session: AsyncSession) -> list[PipelineDestination]:
    """Return managed sink deliveries without creating a second runtime model."""
    return list(
        (
            await session.scalars(
                select(PipelineDestination)
                .order_by(PipelineDestination.created_at.desc())
                .limit(500)
            )
        ).all()
    )


async def delivery_by_id(session: AsyncSession, identifier: uuid.UUID) -> PipelineDestination:
    query = select(PipelineDestination).where(PipelineDestination.id == identifier)
    delivery = await session.scalar(query)
    if delivery is None:
        raise DomainError("NOT_FOUND", "Delivery was not found", 404)
    return delivery


def aggregate(states: list[str]) -> str:
    if not states:
        return "UNKNOWN"
    if all(state == "PAUSED" for state in states):
        return "PAUSED"
    if all(state == "FAILED" for state in states):
        return "FAILED"
    if any(state in {"FAILED", "DEGRADED"} for state in states):
        return "DEGRADED"
    if any(state == "UNKNOWN" for state in states):
        return "UNKNOWN"
    return "RUNNING" if "RUNNING" in states else "PAUSED"


async def delivery_views(session: AsyncSession, values: list[PipelineDestination]) -> list[dict]:
    """Hydrate a bounded delivery collection with a constant number of queries."""
    if not values:
        return []
    destinations = {
        item.id: item
        for item in (
            await session.scalars(
                select(Connection).where(
                    Connection.id.in_({item.destination_id for item in values})
                )
            )
        ).all()
    }
    pipelines = {
        item.id: item
        for item in (
            await session.scalars(
                select(Pipeline).where(Pipeline.id.in_({item.pipeline_id for item in values}))
            )
        ).all()
    }
    connectors = {
        item.id: item
        for item in (
            await session.scalars(
                select(Connector).where(
                    Connector.id.in_({item.connector_id for item in values if item.connector_id})
                )
            )
        ).all()
    }
    clusters = {
        item.id: item
        for item in (
            await session.scalars(
                select(ConnectCluster).where(
                    ConnectCluster.id.in_({item.connect_cluster_id for item in connectors.values()})
                )
            )
        ).all()
    }
    return [
        _delivery_view(
            item,
            destinations[item.destination_id],
            pipelines[item.pipeline_id],
            connectors.get(item.connector_id) if item.connector_id else None,
            clusters,
        )
        for item in values
    ]


async def delivery_view(session: AsyncSession, delivery: PipelineDestination) -> dict:
    return (await delivery_views(session, [delivery]))[0]


def _delivery_view(
    delivery: PipelineDestination,
    destination: Connection,
    pipeline: Pipeline,
    runtime: Connector | None,
    clusters: dict,
) -> dict:
    if not delivery.destination_id:
        raise DomainError("INVALID_DELIVERY", "Database delivery is missing its destination", 500)
    result = serialize(delivery)
    result["destination"] = (
        connection_service.database_view(destination)
        if destination.category == "DATABASE"
        else connection_service.view(destination)
    )
    result["pipeline_name"] = pipeline.name
    result["connector"] = serialize(runtime) if runtime else None
    connector = result["connector"]
    result["connect_cluster"] = (
        serialize(clusters[connector["connect_cluster_id"]]) if connector else None
    )
    result["pipeline"] = serialize(pipeline)
    result["topics"] = [mapping["topic"] for mapping in delivery.topic_mapping_json]
    result["tasks"] = (
        connector.get("runtime_json", {}).get("tasks", [])
        if connector and isinstance(connector.get("runtime_json"), dict)
        else []
    )
    result["metrics"] = {
        "lag": None,
        "throughput": None,
        "last_message": None,
        "notice": "Delivery metrics require a sink metrics provider",
    }
    return result


async def details(session: AsyncSession, values: list[Connection]) -> list[dict]:
    if not values:
        return []
    links = list(
        (
            await session.scalars(
                select(PipelineDestination)
                .where(PipelineDestination.destination_id.in_([item.id for item in values]))
                .order_by(PipelineDestination.created_at)
            )
        ).all()
    )
    views = await delivery_views(session, links)
    grouped: dict[uuid.UUID, list[dict]] = {}
    for link, value in zip(links, views, strict=True):
        grouped.setdefault(link.destination_id, []).append(value)
    return [_destination_detail(item, grouped.get(item.id, [])) for item in values]


async def detail(session: AsyncSession, destination: Destination) -> dict:
    return (await details(session, [destination]))[0]


def _destination_detail(destination: Connection, links: list[dict]) -> dict:
    result = (
        connection_service.database_view(destination)
        if destination.category == "DATABASE"
        else connection_service.view(destination)
    )
    result.update(
        connected_pipelines=len({link["pipeline_id"] for link in links}),
        delivery_count=len(links),
        desired_state=aggregate([link["desired_state"] for link in links]),
        actual_state=aggregate([link["actual_state"] for link in links]),
        last_delivery=None,
        records_per_second=None,
        delivery_lag=None,
        metrics_notice="Delivery metrics and last successful write require a sink metrics provider",
        deliveries=links,
    )
    return result


async def adapter(session: AsyncSession, destination: Destination) -> DestinationAdapter:
    if destination.category != "DATABASE":
        raise DomainError(
            "DATABASE_DESTINATION_REQUIRED", "A database destination is required", 422
        )
    if destination.secret_ref is None:
        raise DomainError(
            "CONNECTION_CREDENTIALS_REQUIRED", "Destination credentials are missing", 422
        )
    secret = await secret_provider(session).get_secret(destination.secret_ref)
    return get_provider(destination.type).destination_adapter(destination, secret["password"])


async def test(session: AsyncSession, destination: Destination, actor: str) -> dict:
    if destination.category == "OBJECT_STORAGE":
        return await connection_service.test(session, destination, actor)
    target = await adapter(session, destination)
    await session.commit()
    try:
        result = await target.test_connection()
        destination.status = "HEALTHY"
    except DomainError as exc:
        destination.status = "UNHEALTHY"
        destination.last_tested_at = now()
        await observe_database_connection(
            session,
            kind="destination",
            identifier=str(destination.id),
            name=destination.name,
            database_type=destination.type,
            firing=True,
            message=exc.message,
        )
        audit(session, actor, "destination.connection_tested", destination)
        await session.commit()
        raise
    destination.last_tested_at = now()
    await observe_database_connection(
        session,
        kind="destination",
        identifier=str(destination.id),
        name=destination.name,
        database_type=destination.type,
        firing=False,
        message="Destination database connection recovered",
    )
    audit(session, actor, "destination.connection_tested", destination)
    errors = (
        await session.scalars(
            select(PipelineEvent).where(
                PipelineEvent.destination_id == destination.id,
                PipelineEvent.category == "DESTINATION",
                PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]),
            )
        )
    ).all()
    for error in errors:
        error.status = "RESOLVED"
    return result


async def test_unsaved(session: AsyncSession, data: DestinationInput) -> dict:
    if not data.password or not data.password.get_secret_value():
        raise DomainError("PASSWORD_REQUIRED", "A destination password is required", 422)
    transient = Destination(**data.model_dump(exclude={"password"}))
    return (
        await get_provider(transient.type)
        .destination_adapter(transient, data.password.get_secret_value())
        .test_connection()
    )


async def update(
    session: AsyncSession, destination: Destination, data: DestinationInput, actor: str
) -> Destination:
    new_config = data.model_dump(exclude={"password", "name", "type", "description"})
    changed = destination.config_json != new_config or bool(
        data.password and data.password.get_secret_value()
    )
    if changed and await deliveries(session, destination.id):
        raise DomainError(
            "DESTINATION_IN_USE",
            "Remove deliveries before changing destination connection settings",
            409,
        )
    return await connection_service.update(
        session,
        destination,
        ConnectionInput(
            name=data.name,
            description=data.description,
            category="DATABASE",
            provider=data.type.upper(),
            config=new_config,
            credentials={"password": data.password} if data.password else {},
            capabilities=list(destination.capabilities_json),
        ),
        actor,
    )


async def delete(session: AsyncSession, destination: Destination, actor: str) -> dict:
    if await deliveries(session, destination.id):
        raise DomainError(
            "DESTINATION_IN_USE", "Remove deliveries before deleting the destination", 409
        )
    for incident in (
        await session.scalars(
            select(PipelineEvent).where(
                PipelineEvent.destination_id == destination.id,
                PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]),
            )
        )
    ).all():
        incident.status = "RESOLVED"
    if set(destination.capabilities_json) != {"DESTINATION"}:
        destination.capabilities_json = [
            capability
            for capability in destination.capabilities_json
            if capability != "DESTINATION"
        ]
        audit(session, actor, "connection.destination_capability_removed", destination)
    else:
        await connection_service.delete(session, destination, actor)
    return {"deleted": True}


async def record_error(
    session: AsyncSession,
    identifier: uuid.UUID,
    error: DomainError,
    pipeline_id: uuid.UUID | None = None,
    connector_id: uuid.UUID | None = None,
    category: str = "DESTINATION",
) -> None:
    if not await session.get(Connection, identifier):
        return
    if pipeline_id and not await session.get(Pipeline, pipeline_id):
        pipeline_id = None
    existing = await session.scalar(
        select(PipelineEvent).where(
            PipelineEvent.destination_id == identifier,
            PipelineEvent.category == category,
            PipelineEvent.message == error.message,
            PipelineEvent.pipeline_id == pipeline_id,
            PipelineEvent.connector_id == connector_id,
            PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]),
        )
    )
    if not existing:
        session.add(
            PipelineEvent(
                destination_id=identifier,
                pipeline_id=pipeline_id,
                connector_id=connector_id,
                category=category,
                severity="error",
                message=error.message,
            )
        )


async def build(
    session: AsyncSession, destination: Destination, data: DeliveryInput, name: str
) -> tuple[dict, ConnectCluster, dict, DestinationAdapter | None]:
    strategy = delivery_provider(destination)
    if data.delivery_type != strategy.delivery_type:
        raise DomainError(
            "DELIVERY_TYPE_MISMATCH",
            f"{destination.provider} requires {strategy.delivery_type} delivery options",
            422,
        )
    pipeline = await get(session, Pipeline, data.pipeline_id)
    if not pipeline.connector_id:
        raise DomainError(
            "PIPELINE_NOT_DEPLOYED", "Deploy the capture pipeline before adding a destination", 422
        )
    cluster = await get(
        session, ConnectCluster, data.connect_cluster_id or pipeline.connect_cluster_id
    )
    if cluster.kafka_cluster_id != pipeline.kafka_cluster_id:
        raise DomainError(
            "CLUSTER_MISMATCH",
            "The sink Connect cluster must belong to the pipeline's Kafka cluster",
            422,
        )
    client = KafkaConnectClient(cluster.base_url)
    await session.commit()
    plugins = await client.plugins()
    if not any(
        plugin["class"] == strategy.connector_class and plugin.get("type") == "sink"
        for plugin in plugins
    ):
        raise DomainError(
            "SINK_PLUGIN_MISSING",
            "Required sink connector plugin is not installed on this Kafka Connect cluster",
            422,
            {"connector_class": strategy.connector_class},
        )
    selected = {
        table.topic_name: table
        for table in (
            await session.scalars(
                select(PipelineTable).where(PipelineTable.pipeline_id == pipeline.id)
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
    kafka = await get(session, KafkaCluster, pipeline.kafka_cluster_id)
    secret_values = await connection_service.credentials(session, destination)
    await session.commit()
    known_topics = {topic["name"] for topic in await KafkaExplorer().topics(kafka)}
    if not strategy.needs_relational_metadata:
        pipeline_topics = set(selected)
        missing_pipeline = set(data.topics) - pipeline_topics
        if missing_pipeline:
            raise DomainError(
                "TOPIC_NOT_IN_PIPELINE",
                "Selected topic does not belong to the capture pipeline",
                422,
                {"topics": sorted(missing_pipeline)},
            )
        missing_runtime = set(data.topics) - known_topics
        if missing_runtime:
            raise DomainError(
                "TOPIC_NOT_FOUND",
                "One or more capture topics are missing",
                422,
                {"topics": sorted(missing_runtime)},
            )
        from app.services.object_storage import test_connection as test_object_storage

        await test_object_storage(destination, secret_values)
        metadata = {
            "event_format": "debezium-envelope",
            "version": 1,
            "delete_semantics": "Delete envelopes are retained; tombstones are not a substitute",
            "topics": list(data.topics),
        }
        config = strategy.build_config(
            destination,
            data,
            metadata,
            name,
            has_session_token=bool(secret_values.get("session_token")),
        )
        await client.validate(config, secrets=list(secret_values.values()))
        return config, cluster, metadata, None
    metadata = {}
    other_links = await deliveries(session, destination.id)
    runtimes = {
        item.id: item
        for item in (
            await session.scalars(
                select(Connector).where(
                    Connector.id.in_(
                        [link.connector_id for link in other_links if link.connector_id]
                    )
                )
            )
        ).all()
    }
    occupied: set[tuple[str, str]] = set()
    for link in other_links:
        if link.delivery_type != "DATABASE":
            continue
        runtime = runtimes.get(link.connector_id) if link.connector_id else None
        if runtime and runtime.name == name:
            continue
        occupied.update(
            (mapping["schema_name"], mapping["table_name"]) for mapping in link.topic_mapping_json
        )
    source_database = await get(session, Connection, pipeline.source_id)
    for mapping in data.mappings:
        if (mapping.schema_name, mapping.table_name) in occupied:
            raise DomainError(
                "TARGET_TABLE_IN_USE",
                "Another delivery on this destination already writes to the selected table",
                422,
                {"table": f"{mapping.schema_name}.{mapping.table_name}"},
            )
        table = selected.get(mapping.topic)
        if table is None:
            raise DomainError(
                "TOPIC_NOT_IN_PIPELINE",
                "Selected topic does not belong to the capture pipeline",
                422,
                {"topic": mapping.topic},
            )
        if mapping.topic not in known_topics:
            raise DomainError(
                "TOPIC_NOT_FOUND",
                "Capture topic is missing; prepare capture topics, then retry delivery validation",
                422,
                {"topic": mapping.topic},
            )
        source = discovered.get((table.schema_name, table.table_name))
        if source is None or not source.primary_key_columns:
            raise DomainError(
                "SOURCE_SCHEMA_REQUIRED",
                "Discover source columns and primary keys before configuring delivery",
                422,
            )
        columns = analyze_columns(source.columns_json, source_database.type, destination.type)
        incompatible = [column for column in columns if column["compatibility"] == "INCOMPATIBLE"]
        if incompatible:
            raise DomainError(
                "DELIVERY_TYPE_UNSUPPORTED",
                "Selected topic contains columns without a safe destination mapping",
                422,
                {"columns": incompatible},
            )
        metadata[mapping.topic] = {
            "columns": columns,
            "primary_keys": source.primary_key_columns,
            "schema_name": mapping.schema_name,
            "table_name": mapping.table_name,
        }
    target = await adapter(session, destination)
    await session.commit()
    await target.test_connection()
    await target.validate(data, metadata)
    config = strategy.build_config(destination, data, metadata, name)
    await client.validate(config)
    return config, cluster, metadata, target


async def preview(session: AsyncSession, destination: Destination, data: DeliveryInput) -> dict:
    config, cluster, metadata, _ = await build(
        session, destination, data, "cluecdc-delivery-preview"
    )
    return {
        "config": redact(config),
        "mappings": [mapping.model_dump() for mapping in data.mappings],
        "connect_cluster_id": cluster.id,
        "connector_class": delivery_provider(destination).connector_class,
        "compatibility": metadata,
    }


def stored_configuration(data: DeliveryInput) -> dict:
    common = {"pipeline_id", "connect_cluster_id", "name", "delivery_type", "tasks_max"}
    object_storage = {
        "output_format",
        "compression",
        "file_max_records",
        "flush_interval_ms",
        "file_name_template",
        "error_policy",
    }
    fields = (
        common | object_storage
        if data.delivery_type == "OBJECT_STORAGE"
        else set(DeliveryInput.model_fields) - object_storage - {"mappings", "topics"}
    )
    return data.model_dump(mode="json", include=fields)


async def deploy(
    session: AsyncSession, destination: Destination, data: DeliveryInput, actor: str
) -> dict:
    identifier = uuid.uuid4()
    name = "cluecdc-delivery-" + identifier.hex
    if await session.scalar(
        select(PipelineDestination.id).where(
            PipelineDestination.destination_id == destination.id,
            PipelineDestination.name == data.name,
        )
    ):
        raise DomainError(
            "DELIVERY_EXISTS", "A delivery with this name already exists on the destination", 409
        )
    config, cluster, metadata, target = await build(session, destination, data, name)
    if target is not None:
        await target.prepare(data, metadata)
    client = KafkaConnectClient(cluster.base_url)
    await client.create(name, config)
    try:
        connector = Connector(
            name=name,
            connector_type="sink",
            connect_cluster_id=cluster.id,
            connector_class=delivery_provider(destination).connector_class,
            config_json=redact(config),
            desired_state="RUNNING",
            actual_state="UNKNOWN",
        )
        session.add(connector)
        await session.flush()
        delivery = PipelineDestination(
            id=identifier,
            pipeline_id=data.pipeline_id,
            destination_id=destination.id,
            connector_id=connector.id,
            name=data.name,
            delivery_type=data.delivery_type,
            delivery_mode=data.write_mode if data.delivery_type == "DATABASE" else "jsonl",
            topic_mapping_json=(
                [mapping.model_dump() for mapping in data.mappings]
                if data.delivery_type == "DATABASE"
                else [{"topic": topic} for topic in data.topics]
            ),
            configuration_json=stored_configuration(data),
            desired_state="RUNNING",
            actual_state="UNKNOWN",
        )
        session.add(delivery)
        await session.flush()
        destination.status = "HEALTHY"
        destination.last_tested_at = now()
        audit(session, actor, "destination.deployed", destination)
        audit(session, actor, "delivery.created", delivery)
        await session.commit()
    except Exception:
        await session.rollback()
        try:
            await client.operate(name, "delete")
        except DomainError:
            log.error("destination_compensation_failed", connector_name=name)
        raise
    return await delivery_view(session, delivery)


async def reconcile(session: AsyncSession, delivery: PipelineDestination) -> dict:
    if not delivery.connector_id:
        return {"actual_state": "UNKNOWN", "tasks": []}
    connector = await get(session, Connector, delivery.connector_id)
    cluster = await get(session, ConnectCluster, connector.connect_cluster_id)
    previous = delivery.actual_state
    await session.commit()
    try:
        status = await KafkaConnectClient(cluster.base_url).status(connector.name)
        state = derive_actual_state(status)
        cluster.status = "HEALTHY"
    except DomainError as error:
        status = {"error": {"code": error.code, "message": error.message}, "tasks": []}
        state = "UNKNOWN"
        cluster.status = "UNAVAILABLE"
    delivery.actual_state = connector.actual_state = state
    connector.runtime_json = status
    if state != previous:
        audit(session, "reconciler", "destination.state_observed", delivery)
        if state in {"FAILED", "DEGRADED", "UNKNOWN"}:
            if not delivery.destination_id:
                raise DomainError("INVALID_DELIVERY", "Database delivery is invalid", 500)
            await record_error(
                session,
                delivery.destination_id,
                DomainError(
                    "SINK_RUNTIME_FAILURE",
                    status.get("error", {}).get("message")
                    or next(
                        (
                            task.get("error")
                            for task in status.get("tasks", [])
                            if task.get("state") == "FAILED" and task.get("error")
                        ),
                        None,
                    )
                    or f"Sink connector runtime state changed to {state}",
                ),
                delivery.pipeline_id,
                connector.id,
                "SINK_CONNECTOR",
            )
        elif state == "RUNNING":
            errors = (
                await session.scalars(
                    select(PipelineEvent).where(
                        PipelineEvent.connector_id == connector.id,
                        PipelineEvent.category == "SINK_CONNECTOR",
                        PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]),
                    )
                )
            ).all()
            for incident in errors:
                incident.status = "RESOLVED"
    pipeline = await get(session, Pipeline, delivery.pipeline_id)
    await observe_connector(
        session,
        pipeline=pipeline,
        connector=connector,
        status=status,
        state=state,
        desired_state=delivery.desired_state,
        delivery=delivery,
    )
    return {
        "delivery_id": delivery.id,
        "pipeline_id": delivery.pipeline_id,
        "name": delivery.name,
        "desired_state": delivery.desired_state,
        "actual_state": state,
        "connector_id": connector.id,
        **status,
    }


async def operate(
    session: AsyncSession,
    destination: Destination,
    delivery: PipelineDestination,
    operation: str,
    actor: str,
    task: int | None = None,
) -> dict:
    if not delivery.connector_id:
        raise DomainError("NOT_DEPLOYED", "Delivery has no connector", 409)
    connector = await get(session, Connector, delivery.connector_id)
    cluster = await get(session, ConnectCluster, connector.connect_cluster_id)
    await session.commit()
    try:
        await KafkaConnectClient(cluster.base_url).operate(connector.name, operation, task)
    except DomainError as error:
        if operation != "delete" or error.code != "CONNECTOR_NOT_FOUND":
            raise
    if operation == "delete":
        for incident in (
            await session.scalars(
                select(PipelineEvent).where(
                    PipelineEvent.connector_id == connector.id,
                    PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]),
                )
            )
        ).all():
            incident.status = "RESOLVED"
        audit(session, actor, "delivery.deleted", delivery)
        await session.delete(delivery)
        await session.flush()
        await session.delete(connector)
        await session.commit()
        return {"deleted": True}
    if operation in {"pause", "resume"}:
        delivery.desired_state = connector.desired_state = (
            "PAUSED" if operation == "pause" else "RUNNING"
        )
    action = {
        "pause": "paused",
        "resume": "resumed",
        "restart": "restarted",
        "restart-task": "restarted",
    }[operation]
    audit(session, actor, f"destination.{action}", destination)
    audit(session, actor, f"delivery.{action}", delivery)
    await session.commit()
    return {
        "delivery_id": delivery.id,
        "desired_state": delivery.desired_state,
        "actual_state": delivery.actual_state,
    }


async def update_mapping(
    session: AsyncSession,
    destination: Destination,
    delivery: PipelineDestination,
    data: DeliveryInput,
    actor: str,
) -> dict:
    if data.pipeline_id != delivery.pipeline_id:
        raise DomainError(
            "PIPELINE_IMMUTABLE", "A delivery's source pipeline cannot be changed", 422
        )
    if not delivery.connector_id:
        raise DomainError("NOT_DEPLOYED", "Delivery has no connector", 409)
    connector = await get(session, Connector, delivery.connector_id)
    config, cluster, metadata, target = await build(session, destination, data, connector.name)
    if cluster.id != connector.connect_cluster_id:
        raise DomainError(
            "CLUSTER_IMMUTABLE",
            "Remove and redeploy to move a delivery to another Connect cluster",
            422,
        )
    if target is not None:
        await target.prepare(data, metadata)
    # Reconstruct the previous secret reference for compensating runtime update.
    previous_config = dict(connector.config_json)
    if delivery.delivery_type == "DATABASE":
        previous_config["connection.password"] = f"${{cluecdc:{destination.secret_ref}:password}}"
    else:
        if "cluecdc.session.token" in previous_config:
            previous_config["aws.credentials.provider"] = (
                "io.cluecdc.connect.ClueSessionCredentialsProvider"
            )
            for config_key, secret_key in {
                "cluecdc.access.key": "access_key",
                "cluecdc.secret.key": "secret_key",
                "cluecdc.session.token": "session_token",
            }.items():
                previous_config[config_key] = f"${{cluecdc:{destination.secret_ref}:{secret_key}}}"
        else:
            previous_config["aws.access.key.id"] = (
                f"${{cluecdc:{destination.secret_ref}:access_key}}"
            )
            previous_config["aws.secret.access.key"] = (
                f"${{cluecdc:{destination.secret_ref}:secret_key}}"
            )
    client = KafkaConnectClient(cluster.base_url)
    before = serialize(delivery)
    await client.update(connector.name, config)
    try:
        connector.config_json = redact(config)
        delivery.topic_mapping_json = (
            [mapping.model_dump() for mapping in data.mappings]
            if data.delivery_type == "DATABASE"
            else [{"topic": topic} for topic in data.topics]
        )
        delivery.configuration_json = stored_configuration(data)
        delivery.delivery_mode = data.write_mode if data.delivery_type == "DATABASE" else "jsonl"
        delivery.name = data.name
        audit(session, actor, "destination.mapping_updated", destination)
        audit(session, actor, "delivery.mapping_updated", delivery, before)
        await session.commit()
    except Exception:
        await session.rollback()
        try:
            await client.update(connector.name, previous_config)
        except DomainError:
            log.error("destination_update_compensation_failed", connector_name=connector.name)
        raise
    return await delivery_view(session, delivery)
