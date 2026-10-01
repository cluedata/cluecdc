import asyncio
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.connect import KafkaConnectClient
from app.adapters.kafka import KafkaExplorer
from app.core.auth import PERMISSIONS, Principal, principal, require
from app.core.config import get_settings
from app.core.database import session_dependency
from app.core.errors import DomainError
from app.models.entities import (
    AuditLog,
    ConnectCluster,
    Connector,
    Destination,
    Job,
    KafkaCluster,
    LakehouseTarget,
    Pipeline,
    PipelineDestination,
    PipelineEvent,
    PipelineOperation,
    PipelineTable,
    SchemaVersion,
    SecretReference,
    Source,
    SourceTable,
)
from app.providers import provider_metadata, provider_metadata_for
from app.providers.server_ids import assign_mysql_server_id
from app.repositories.metadata import audit, get, listing, serialize
from app.schemas.requests import (
    AddPipelineTablesInput,
    ConnectInput,
    DeliveryInput,
    KafkaInput,
    PipelineInput,
    RemovePipelineTableInput,
    ResyncPipelineTableInput,
    SourceInput,
)
from app.services import lakehouse as lakehouse_service
from app.services import pipeline as pipeline_service
from app.services import pipeline_tables as pipeline_table_service
from app.services import source as source_service
from app.services.destinations import service as destination_service
from app.services.secrets import secret_provider

router = APIRouter(prefix="/api/v1")
DB = Annotated[AsyncSession, Depends(session_dependency)]
event_slots = asyncio.Semaphore(4)


@router.get("/database-providers")
async def database_providers(user: Annotated[Principal, Depends(require("sources.read"))]):
    return provider_metadata()


@router.get("/session")
async def session_info(user: Annotated[Principal, Depends(principal)]):
    return {
        "actor": user.actor,
        "role": user.role,
        "auth_mode": get_settings().auth_mode,
        "environment": get_settings().environment,
        "permissions": sorted(PERMISSIONS[user.role]),
    }


@router.get("/sources")
async def sources(db: DB, user: Annotated[Principal, Depends(require("sources.read"))]):
    results = await listing(db, Source)
    for source in results:
        counts = (
            await db.execute(
                select(
                    func.count(),
                    func.sum(SourceTable.cdc_ready.cast(__import__("sqlalchemy").Integer)),
                ).where(SourceTable.source_id == source["id"])
            )
        ).one()
        source.update(tables=counts[0], cdc_ready_tables=counts[1] or 0)
    return results


@router.post("/sources", status_code=201)
async def create_source(
    data: SourceInput, db: DB, user: Annotated[Principal, Depends(require("sources.write"))]
):
    if data.password is None or not data.password.get_secret_value():
        raise DomainError("PASSWORD_REQUIRED", "A source password is required", 422)
    secret_ref = await secret_provider(db).put_secret(
        {"password": data.password.get_secret_value()}
    )
    source = Source(**data.model_dump(exclude={"password"}), secret_ref=secret_ref)
    db.add(source)
    await db.flush()
    if source.type == "mysql":
        options = dict(source.provider_options)
        options["server_id"] = await assign_mysql_server_id(db, source.id, options.get("server_id"))
        source.provider_options = options
    audit(db, user.actor, "source.created", source)
    await db.commit()
    return serialize(source)


@router.get("/sources/{identifier}")
async def source_detail(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("sources.read"))]
):
    result = serialize(await get(db, Source, identifier))
    counts = (
        await db.execute(
            select(
                func.count(), func.sum(case((SourceTable.cdc_ready.is_(True), 1), else_=0))
            ).where(SourceTable.source_id == identifier)
        )
    ).one()
    result.update(tables=counts[0], cdc_ready_tables=counts[1] or 0)
    result["last_discovery_at"] = await db.scalar(
        select(func.max(AuditLog.created_at)).where(
            AuditLog.resource_id == str(identifier),
            AuditLog.action == "source.discovered",
        )
    )
    return result


@router.put("/sources/{identifier}")
async def update_source(
    identifier: UUID,
    data: SourceInput,
    db: DB,
    user: Annotated[Principal, Depends(require("sources.write"))],
):
    source = await get(db, Source, identifier)
    if await db.scalar(select(Pipeline.id).where(Pipeline.source_id == identifier).limit(1)):
        raise DomainError(
            "SOURCE_IN_USE",
            "Delete associated pipelines before changing connection configuration",
            409,
        )
    before = serialize(source)
    previous_options = dict(source.provider_options)
    for k, v in data.model_dump(exclude={"password"}).items():
        setattr(source, k, v)
    if source.type == "mysql":
        options = dict(source.provider_options)
        options["server_id"] = await assign_mysql_server_id(
            db,
            source.id,
            options.get("server_id") or previous_options.get("server_id"),
            exclude_source_id=source.id,
        )
        source.provider_options = options
    if data.password and data.password.get_secret_value():
        old = await get(db, SecretReference, source.secret_ref)
        source.secret_ref = await secret_provider(db).put_secret(
            {"password": data.password.get_secret_value()}
        )
        await db.delete(old)
    source.status = "UNKNOWN"
    source.last_health_check_at = None
    # A changed connection must be discovered again before its tables are eligible.
    await db.execute(delete(SourceTable).where(SourceTable.source_id == identifier))
    audit(db, user.actor, "source.updated", source, before)
    await db.commit()
    return serialize(source)


@router.delete("/sources/{identifier}")
async def delete_source(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("sources.write"))]
):
    source = await get(db, Source, identifier)
    if await db.scalar(select(Pipeline.id).where(Pipeline.source_id == identifier).limit(1)):
        raise DomainError(
            "SOURCE_IN_USE", "Delete associated pipelines before deleting this source", 409
        )
    secret = await get(db, SecretReference, source.secret_ref)
    audit(db, user.actor, "source.deleted", source)
    await db.delete(source)
    await db.flush()
    await db.delete(secret)
    await db.commit()
    return {"deleted": True}


@router.post("/sources/{identifier}/test")
async def test_source(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("sources.write"))]
):
    result = await source_service.health(db, identifier, user.actor)
    await db.commit()
    return result


async def enqueue(db: AsyncSession, kind: str, identifier: UUID, actor: str):
    await get(db, Source, identifier)
    existing = await db.scalar(
        select(Job)
        .where(
            Job.resource_id == identifier, Job.kind == kind, Job.status.in_(["PENDING", "RUNNING"])
        )
        .limit(1)
    )
    if existing:
        return serialize(existing)
    job = Job(kind=kind, resource_id=identifier, actor=actor)
    db.add(job)
    await db.flush()
    audit(db, actor, "job.enqueued", job)
    await db.commit()
    return serialize(job)


@router.post("/sources/{identifier}/discover", status_code=202)
async def discover_source(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("sources.write"))]
):
    return await enqueue(db, "discover", identifier, user.actor)


@router.post("/sources/{identifier}/health", status_code=202)
async def health_source(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("sources.write"))]
):
    return await enqueue(db, "health", identifier, user.actor)


@router.get("/jobs/{identifier}")
async def job_status(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("sources.read"))]
):
    return serialize(await get(db, Job, identifier))


@router.get("/sources/{identifier}/cdc-readiness")
async def readiness(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("sources.read"))]
):
    source = await get(db, Source, identifier)
    return await (await source_service.adapter(db, source)).readiness()


@router.get("/sources/{identifier}/tables")
async def tables(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("sources.read"))],
    search: str = "",
    schema: str | None = None,
    cdc_ready: bool | None = None,
    has_primary_key: bool | None = None,
    min_size: int | None = None,
):
    await get(db, Source, identifier)
    query = select(SourceTable).where(SourceTable.source_id == identifier)
    if schema:
        query = query.where(SourceTable.schema_name == schema)
    if cdc_ready is not None:
        query = query.where(SourceTable.cdc_ready == cdc_ready)
    if min_size is not None:
        query = query.where(SourceTable.estimated_size_bytes >= min_size)
    results = (
        await db.scalars(query.order_by(SourceTable.schema_name, SourceTable.table_name).limit(500))
    ).all()
    return [
        serialize(t)
        for t in results
        if search.lower() in f"{t.schema_name}.{t.table_name}".lower()
        and (has_primary_key is None or bool(t.primary_key_columns) == has_primary_key)
    ]


@router.get("/sources/{identifier}/tables/{table_id}/cdc-readiness")
async def table_readiness(
    identifier: UUID,
    table_id: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("sources.read"))],
):
    source = await get(db, Source, identifier)
    table = await get(db, SourceTable, table_id)
    if table.source_id != source.id:
        raise DomainError("NOT_FOUND", "Source table was not found", 404)
    return await (await source_service.adapter(db, source)).inspect_table(
        table.schema_name, table.table_name
    )


@router.get("/sources/{identifier}/namespaces")
async def namespaces(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("sources.read"))],
):
    source = await get(db, Source, identifier)
    values = (
        await db.scalars(
            select(SourceTable.schema_name)
            .where(SourceTable.source_id == identifier)
            .distinct()
            .order_by(SourceTable.schema_name)
        )
    ).all()
    return {
        "label": provider_metadata_for(source.type)["namespace_label"],
        "namespaces": [{"name": value} for value in values],
    }


@router.get("/sources/{identifier}/tables/{table_id}/schema")
async def table_schema(
    identifier: UUID,
    table_id: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("sources.read"))],
):
    table = await get(db, SourceTable, table_id)
    if table.source_id != identifier:
        raise DomainError("NOT_FOUND", "Source table was not found", 404)
    return {
        "namespace": table.schema_name,
        "name": table.table_name,
        "columns": table.columns_json,
        "primary_key": table.primary_key_columns,
        "indexes": table.indexes_json,
    }


@router.get("/kafka/clusters")
async def kafka_clusters(db: DB, user: Annotated[Principal, Depends(require("kafka.read"))]):
    return await listing(db, KafkaCluster)


@router.post("/kafka/clusters", status_code=201)
async def create_kafka(
    data: KafkaInput, db: DB, user: Annotated[Principal, Depends(require("settings.manage"))]
):
    cluster = KafkaCluster(**data.model_dump())
    db.add(cluster)
    await db.flush()
    audit(db, user.actor, "kafka_cluster.created", cluster)
    await db.commit()
    return serialize(cluster)


@router.put("/kafka/clusters/{identifier}")
async def update_kafka(
    identifier: UUID,
    data: KafkaInput,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    cluster = await get(db, KafkaCluster, identifier)
    if await db.scalar(select(Pipeline.id).where(Pipeline.kafka_cluster_id == identifier).limit(1)):
        raise DomainError(
            "CLUSTER_IN_USE", "Delete associated pipelines before changing Kafka configuration", 409
        )
    before = serialize(cluster)
    for k, v in data.model_dump().items():
        setattr(cluster, k, v)
    cluster.status = "UNKNOWN"
    audit(db, user.actor, "kafka_cluster.updated", cluster, before)
    await db.commit()
    return serialize(cluster)


@router.delete("/kafka/clusters/{identifier}")
async def delete_kafka(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("settings.manage"))]
):
    cluster = await get(db, KafkaCluster, identifier)
    audit(db, user.actor, "kafka_cluster.deleted", cluster)
    await db.delete(cluster)
    await db.commit()
    return {"deleted": True}


@router.get("/connect/clusters")
async def connect_clusters(db: DB, user: Annotated[Principal, Depends(require("connect.read"))]):
    return await listing(db, ConnectCluster)


@router.post("/connect/clusters", status_code=201)
async def create_connect(
    data: ConnectInput, db: DB, user: Annotated[Principal, Depends(require("settings.manage"))]
):
    await get(db, KafkaCluster, data.kafka_cluster_id)
    cluster = ConnectCluster(**data.model_dump())
    db.add(cluster)
    await db.flush()
    audit(db, user.actor, "connect_cluster.created", cluster)
    await db.commit()
    return serialize(cluster)


@router.put("/connect/clusters/{identifier}")
async def update_connect(
    identifier: UUID,
    data: ConnectInput,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    cluster = await get(db, ConnectCluster, identifier)
    await get(db, KafkaCluster, data.kafka_cluster_id)
    if await db.scalar(
        select(Pipeline.id).where(Pipeline.connect_cluster_id == identifier).limit(1)
    ) or await db.scalar(
        select(Connector.id).where(Connector.connect_cluster_id == identifier).limit(1)
    ):
        raise DomainError(
            "CLUSTER_IN_USE",
            "Remove associated pipelines and connectors before changing Connect configuration",
            409,
        )
    before = serialize(cluster)
    for k, v in data.model_dump().items():
        setattr(cluster, k, v)
    cluster.status = "UNKNOWN"
    audit(db, user.actor, "connect_cluster.updated", cluster, before)
    await db.commit()
    return serialize(cluster)


@router.delete("/connect/clusters/{identifier}")
async def delete_connect(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("settings.manage"))]
):
    cluster = await get(db, ConnectCluster, identifier)
    audit(db, user.actor, "connect_cluster.deleted", cluster)
    await db.delete(cluster)
    await db.commit()
    return {"deleted": True}


@router.get("/connect/connectors")
async def connectors(db: DB, user: Annotated[Principal, Depends(require("connect.read"))]):
    results = await listing(db, Connector)
    for result in results:
        pipeline = await db.scalar(select(Pipeline).where(Pipeline.connector_id == result["id"]))
        delivery = await db.scalar(
            select(PipelineDestination).where(PipelineDestination.connector_id == result["id"])
        )
        result["related_resource"] = (
            {"name": pipeline.name, "href": f"/pipelines/{pipeline.id}"} if pipeline else None
        )
        if delivery:
            result["related_resource"] = {
                "name": delivery.name,
                "href": f"/deliveries/{delivery.id}",
            }
    return results


@router.get("/deliveries")
async def deliveries(db: DB, user: Annotated[Principal, Depends(require("destinations.read"))]):
    """Expose managed sink connectors as first-class delivery resources."""
    return [
        await lakehouse_service.delivery_view(db, delivery)
        if delivery.delivery_type == "ICEBERG"
        else await destination_service.delivery_view(db, delivery)
        for delivery in await destination_service.all_deliveries(db)
    ]


@router.get("/deliveries/{identifier}")
async def delivery_detail(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("destinations.read"))],
):
    delivery = await destination_service.delivery_by_id(db, identifier)
    return (
        await lakehouse_service.delivery_view(db, delivery)
        if delivery.delivery_type == "ICEBERG"
        else await destination_service.delivery_view(db, delivery)
    )


@router.get("/deliveries/{identifier}/status")
async def delivery_status(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("destinations.read"))],
):
    delivery = await destination_service.delivery_by_id(db, identifier, lock=True)
    result = (
        await lakehouse_service.reconcile(db, delivery)
        if delivery.delivery_type == "ICEBERG"
        else await destination_service.reconcile(db, delivery)
    )
    await db.commit()
    return result


@router.put("/deliveries/{identifier}/mappings")
async def update_delivery_mappings(
    identifier: UUID,
    data: DeliveryInput,
    db: DB,
    user: Annotated[Principal, Depends(require("destinations.operate"))],
):
    delivery = await destination_service.delivery_by_id(db, identifier)
    if delivery.delivery_type == "ICEBERG":
        raise DomainError(
            "ICEBERG_MAPPING_IMMUTABLE",
            "Update Iceberg table routing by redeploying the delivery",
            422,
        )
    if not delivery.destination_id:
        raise DomainError("INVALID_DELIVERY", "Database delivery is invalid", 500)
    destination = await destination_service.locked(db, delivery.destination_id)
    delivery = await destination_service.locked_delivery(db, destination.id, identifier)
    return await destination_service.update_mapping(db, destination, delivery, data, user.actor)


@router.post("/deliveries/{identifier}/{operation}")
async def operate_delivery(
    identifier: UUID,
    operation: str,
    db: DB,
    user: Annotated[Principal, Depends(require("destinations.operate"))],
    task: int | None = Query(default=None, ge=0),
):
    if operation not in {"pause", "resume", "restart", "restart-task"}:
        raise DomainError("INVALID_OPERATION", "Unsupported delivery operation", 404)
    delivery = await destination_service.delivery_by_id(db, identifier)
    if delivery.delivery_type == "ICEBERG":
        delivery = await destination_service.delivery_by_id(db, identifier, lock=True)
        return await lakehouse_service.operate(db, delivery, operation, user.actor, task)
    if not delivery.destination_id:
        raise DomainError("INVALID_DELIVERY", "Database delivery is invalid", 500)
    destination = await destination_service.locked(db, delivery.destination_id)
    delivery = await destination_service.locked_delivery(db, destination.id, identifier)
    return await destination_service.operate(db, destination, delivery, operation, user.actor, task)


@router.delete("/deliveries/{identifier}")
async def delete_delivery(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("destinations.operate"))],
):
    delivery = await destination_service.delivery_by_id(db, identifier)
    if delivery.delivery_type == "ICEBERG":
        delivery = await destination_service.delivery_by_id(db, identifier, lock=True)
        return await lakehouse_service.operate(db, delivery, "delete", user.actor)
    if not delivery.destination_id:
        raise DomainError("INVALID_DELIVERY", "Database delivery is invalid", 500)
    destination = await destination_service.locked(db, delivery.destination_id)
    delivery = await destination_service.locked_delivery(db, destination.id, identifier)
    return await destination_service.operate(db, destination, delivery, "delete", user.actor)


@router.get("/connect/clusters/{identifier}/plugins")
async def plugins(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("connect.read"))]
):
    cluster = await get(db, ConnectCluster, identifier)
    return await KafkaConnectClient(cluster.base_url).plugins()


@router.get("/connect/connectors/{name}/status")
async def connector_status(
    name: str,
    cluster_id: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("connect.read"))],
):
    cluster = await get(db, ConnectCluster, cluster_id)
    return await KafkaConnectClient(cluster.base_url).status(name)


@router.get("/pipelines")
async def pipelines(
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.read"))],
    source_id: UUID | None = None,
):
    query = select(Pipeline).order_by(Pipeline.created_at.desc()).limit(500)
    if source_id:
        query = query.where(Pipeline.source_id == source_id)
    results = [serialize(p) for p in (await db.scalars(query)).all()]
    for p in results:
        pipeline_tables = (
            await db.scalars(select(PipelineTable).where(PipelineTable.pipeline_id == p["id"]))
        ).all()
        p["tables"] = len(pipeline_tables)
        p["topics"] = [table.topic_name for table in pipeline_tables]
        p["throughput"] = p["cdc_lag"] = p["last_event"] = None
        source = await get(db, Source, p["source_id"])
        p["source_name"] = source.name
        kafka = await get(db, KafkaCluster, p["kafka_cluster_id"])
        p["kafka_name"] = kafka.name
        links = (
            await db.scalars(
                select(PipelineDestination).where(PipelineDestination.pipeline_id == p["id"])
            )
        ).all()
        p["destinations"] = len(
            {(link.destination_id, link.lakehouse_destination_id) for link in links}
        )
        p["delivery_state"] = (
            destination_service.aggregate([link.actual_state for link in links])
            if links
            else "NOT_CONFIGURED"
        )
        summaries = []
        for link in links:
            if link.delivery_type == "ICEBERG":
                if not link.lakehouse_destination_id:
                    raise DomainError("INVALID_DELIVERY", "Lakehouse delivery is invalid", 500)
                target = await get(db, LakehouseTarget, link.lakehouse_destination_id)
            else:
                if not link.destination_id:
                    raise DomainError("INVALID_DELIVERY", "Database delivery is invalid", 500)
                target = await get(db, Destination, link.destination_id)
            summaries.append(
                {
                    "id": link.id,
                    "name": link.name,
                    "destination_id": target.id,
                    "destination_name": target.name,
                    "delivery_type": link.delivery_type,
                    "actual_state": link.actual_state,
                }
            )
        p["deliveries_summary"] = summaries
        states = [p["actual_state"], *[link.actual_state for link in links]]
        if p["actual_state"] in {"FAILED", "ERROR", "UNAVAILABLE"}:
            p["aggregate_status"] = "FAILED"
            p["status_reason"] = "Capture connector failed"
        elif any(link.actual_state == "FAILED" for link in links):
            p["aggregate_status"] = "FAILED"
            p["status_reason"] = "A delivery connector failed"
        elif any(state in {"PAUSED", "STOPPED"} for state in states):
            p["aggregate_status"] = "PAUSED"
            p["status_reason"] = "A connector is paused"
        elif not links:
            p["aggregate_status"] = "DEGRADED"
            p["status_reason"] = "No delivery is configured"
        elif any(state in {"DEGRADED", "WARNING"} for state in states):
            p["aggregate_status"] = "DEGRADED"
            p["status_reason"] = "A runtime component is degraded"
        elif any(state in {"UNKNOWN", "DEPLOYING"} for state in states):
            p["aggregate_status"] = "UNKNOWN"
            p["status_reason"] = "Runtime health is not available"
        else:
            p["aggregate_status"] = "HEALTHY"
            p["status_reason"] = "All critical components are running"
    return results


@router.post("/pipelines/preview")
async def preview_pipeline(
    data: PipelineInput, db: DB, user: Annotated[Principal, Depends(require("pipelines.write"))]
):
    return await pipeline_service.preview(db, data)


@router.post("/pipelines", status_code=201)
async def create_pipeline(
    data: PipelineInput, db: DB, user: Annotated[Principal, Depends(require("pipelines.write"))]
):
    p = await pipeline_service.create(db, data, user.actor)
    await db.commit()
    return serialize(p)


@router.get("/pipelines/{identifier}")
async def pipeline_detail(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("pipelines.read"))]
):
    p = await get(db, Pipeline, identifier)
    result = serialize(p)
    result["source"] = serialize(await get(db, Source, p.source_id))
    result["kafka_cluster"] = serialize(await get(db, KafkaCluster, p.kafka_cluster_id))
    result["connect_cluster"] = serialize(await get(db, ConnectCluster, p.connect_cluster_id))
    result["connector"] = (
        serialize(await get(db, Connector, p.connector_id)) if p.connector_id else None
    )
    result["tables"] = [
        serialize(t)
        for t in (
            await db.scalars(
                select(PipelineTable).where(
                    PipelineTable.pipeline_id == identifier,
                    PipelineTable.cdc_status != "REMOVED",
                )
            )
        ).all()
    ]
    result["config"] = (
        await pipeline_service.preview(db, await pipeline_service.input_for(db, p))
    )["config"]
    result["metrics"] = {
        "throughput": None,
        "cdc_lag": None,
        "last_event_timestamp": None,
        "snapshot_state": "UNAVAILABLE",
        "processed_rows": None,
        "notice": "Incremental snapshot status is sourced from Debezium notifications",
    }
    result["destinations"] = [
        await lakehouse_service.delivery_view(db, link)
        if link.delivery_type == "ICEBERG"
        else await destination_service.delivery_view(db, link)
        for link in (
            await db.scalars(
                select(PipelineDestination).where(PipelineDestination.pipeline_id == identifier)
            )
        ).all()
    ]
    return result


@router.get("/pipelines/{identifier}/destinations")
async def pipeline_destinations(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("pipelines.read"))]
):
    await get(db, Pipeline, identifier)
    return [
        await lakehouse_service.delivery_view(db, link)
        if link.delivery_type == "ICEBERG"
        else await destination_service.delivery_view(db, link)
        for link in (
            await db.scalars(
                select(PipelineDestination).where(PipelineDestination.pipeline_id == identifier)
            )
        ).all()
    ]


@router.get("/pipelines/{identifier}/tables")
async def pipeline_tables(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.read"))],
    include_removed: bool = False,
):
    await get(db, Pipeline, identifier)
    query = select(PipelineTable).where(PipelineTable.pipeline_id == identifier)
    if not include_removed:
        query = query.where(PipelineTable.cdc_status != "REMOVED")
    return [serialize(table) for table in (await db.scalars(query)).all()]


@router.post("/pipelines/{identifier}/tables", status_code=202)
async def add_pipeline_tables(
    identifier: UUID,
    data: AddPipelineTablesInput,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
):
    operations = await pipeline_table_service.enqueue_add(db, identifier, data, user.actor)
    await db.commit()
    return [serialize(operation) for operation in operations]


@router.delete("/pipelines/{identifier}/tables/{table_id}", status_code=202)
async def remove_pipeline_table(
    identifier: UUID,
    table_id: UUID,
    data: RemovePipelineTableInput,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
):
    table = await get(db, PipelineTable, table_id)
    if table.pipeline_id != identifier:
        raise DomainError("NOT_FOUND", "Pipeline table was not found", 404)
    operation = await pipeline_table_service.enqueue_remove(db, table_id, data, user.actor)
    await db.commit()
    return serialize(operation)


@router.post("/pipeline-tables/{table_id}/resync", status_code=202)
async def resync_pipeline_table(
    table_id: UUID,
    data: ResyncPipelineTableInput,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
):
    operation = await pipeline_table_service.enqueue_resync(db, table_id, data, user.actor)
    await db.commit()
    return serialize(operation)


@router.post("/pipeline-tables/{table_id}/snapshot/stop", status_code=202)
async def stop_pipeline_table_snapshot(
    table_id: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
):
    operation = await pipeline_table_service.stop_active_snapshot(db, table_id, user.actor)
    await db.commit()
    return serialize(operation)


@router.get("/pipelines/{identifier}/operations")
async def pipeline_operations(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.read"))],
):
    await get(db, Pipeline, identifier)
    query = (
        select(PipelineOperation)
        .where(PipelineOperation.pipeline_id == identifier)
        .order_by(PipelineOperation.created_at.desc())
        .limit(200)
    )
    return [serialize(operation) for operation in (await db.scalars(query)).all()]


@router.get("/operations/{identifier:uuid}")
async def operation_detail(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.read"))],
):
    return serialize(await get(db, PipelineOperation, identifier))


@router.post("/operations/{identifier:uuid}/retry", status_code=202)
async def retry_operation(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
):
    operation = await get(db, PipelineOperation, identifier)
    await pipeline_table_service.retry_failed_operation(db, operation, user.actor)
    await db.commit()
    return serialize(operation)


@router.get("/pipelines/{identifier}/status")
async def pipeline_status(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("pipelines.read"))]
):
    p = await pipeline_service.locked(db, identifier)
    result = await pipeline_service.reconcile(db, p)
    await db.commit()
    return {"desired_state": p.desired_state, **result}


@router.post("/pipelines/{identifier}/deploy")
async def deploy_pipeline(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("pipelines.operate"))]
):
    return await pipeline_service.deploy(db, identifier, user.actor)


@router.post("/pipelines/{identifier}/prepare-topics")
async def prepare_pipeline_topics(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("pipelines.operate"))]
):
    pipeline = await pipeline_service.locked(db, identifier)
    result = await pipeline_service.prepare_topics(db, pipeline, user.actor)
    await db.commit()
    return result


@router.post("/pipelines/{identifier}/{operation}")
async def operate_pipeline(
    identifier: UUID,
    operation: str,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
    task: int | None = Query(default=None, ge=0),
):
    if operation not in {"pause", "resume", "restart", "restart-task"}:
        raise DomainError("INVALID_OPERATION", "Unsupported pipeline operation", 404)
    result = await pipeline_service.operate(db, identifier, operation, user.actor, task)
    await db.commit()
    return result


@router.delete("/pipelines/{identifier}")
async def delete_pipeline(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("pipelines.write"))]
):
    result = await pipeline_service.operate(db, identifier, "delete", user.actor)
    await db.commit()
    return result


@router.get("/kafka/topics")
async def topics(
    cluster_id: UUID, db: DB, user: Annotated[Principal, Depends(require("kafka.read"))]
):
    cluster = await get(db, KafkaCluster, cluster_id)
    try:
        result = await KafkaExplorer().topics(cluster)
        cluster.status = "HEALTHY"
        await db.commit()
        return result
    except DomainError:
        cluster.status = "UNAVAILABLE"
        await db.commit()
        raise


@router.get("/kafka/consumer-groups")
async def consumer_groups(
    db: DB,
    user: Annotated[Principal, Depends(require("kafka.read"))],
    cluster_id: UUID | None = None,
):
    query = select(KafkaCluster).order_by(KafkaCluster.name)
    if cluster_id:
        query = query.where(KafkaCluster.id == cluster_id)
    clusters = (await db.scalars(query)).all()
    if cluster_id and not clusters:
        raise DomainError("NOT_FOUND", "KafkaCluster was not found", 404)

    async def inspect(cluster: KafkaCluster) -> dict:
        try:
            return {
                "cluster": cluster,
                "groups": await KafkaExplorer().consumer_groups(cluster),
                "error": None,
            }
        except DomainError as error:
            return {
                "cluster": cluster,
                "groups": [],
                "error": {"code": error.code, "message": error.message},
            }

    observations = await asyncio.gather(*(inspect(cluster) for cluster in clusters))
    resource_by_group: dict[tuple[UUID, str], dict] = {}
    if clusters:
        links = (
            await db.execute(
                select(Connector, PipelineDestination, ConnectCluster)
                .join(
                    PipelineDestination,
                    PipelineDestination.connector_id == Connector.id,
                )
                .join(ConnectCluster, ConnectCluster.id == Connector.connect_cluster_id)
                .where(ConnectCluster.kafka_cluster_id.in_([cluster.id for cluster in clusters]))
            )
        ).all()
        for connector, delivery, connect_cluster in links:
            explicit_group = connector.config_json.get("consumer.override.group.id")
            resource = {
                "connector_name": connector.name,
                "resource_type": "delivery",
                "resource_id": str(delivery.id),
                "resource_name": delivery.name,
            }
            for group_id in {
                explicit_group,
                connector.name,
                f"connect-{connector.name}",
            }:
                if group_id:
                    resource_by_group[(connect_cluster.kafka_cluster_id, group_id)] = resource

    groups = []
    cluster_reports = []
    for observation in observations:
        cluster = observation["cluster"]
        cluster_reports.append(
            {
                "id": str(cluster.id),
                "name": cluster.name,
                "status": "UNAVAILABLE" if observation["error"] else "HEALTHY",
                "group_count": len(observation["groups"]),
                "error": observation["error"],
            }
        )
        for group in observation["groups"]:
            groups.append(
                {
                    **group,
                    "cluster_id": str(cluster.id),
                    "cluster_name": cluster.name,
                    **resource_by_group.get((cluster.id, group["group_id"]), {}),
                }
            )
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "clusters": cluster_reports,
        "groups": groups,
    }


@router.get("/kafka/topics/{name}")
async def topic(
    name: str, cluster_id: UUID, db: DB, user: Annotated[Principal, Depends(require("kafka.read"))]
):
    results = await KafkaExplorer().topics(await get(db, KafkaCluster, cluster_id), name)
    if not results:
        raise DomainError("TOPIC_NOT_FOUND", "Kafka topic was not found", 404)
    result = results[0]
    usage = (
        await db.execute(
            select(Pipeline.id, Pipeline.name, Pipeline.desired_state, Pipeline.actual_state)
            .join(PipelineTable, PipelineTable.pipeline_id == Pipeline.id)
            .where(
                Pipeline.kafka_cluster_id == cluster_id,
                PipelineTable.topic_name == name,
                PipelineTable.cdc_status != "REMOVED",
            )
            .distinct()
            .order_by(Pipeline.name)
        )
    ).all()
    result["used_by_pipelines"] = [
        {
            "id": str(row.id),
            "name": row.name,
            "desired_state": row.desired_state,
            "actual_state": row.actual_state,
            "active": row.desired_state == "RUNNING" or row.actual_state == "RUNNING",
        }
        for row in usage
    ]
    return result


@router.delete("/kafka/topics/{name}")
async def delete_topic(
    name: str,
    cluster_id: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("kafka.topic.delete"))],
):
    cluster = await get(db, KafkaCluster, cluster_id)
    audit_base = {"topic": name, "cluster_id": str(cluster.id), "cluster_name": cluster.name}
    try:
        result = await KafkaExplorer().delete_topic(cluster, name)
    except DomainError as exc:
        db.add(
            AuditLog(
                actor=user.actor,
                action="kafka.topic.delete.failed",
                resource_type="kafka_topic",
                resource_id=name,
                before_json=audit_base,
                after_json={"result": "failed", "error_code": exc.code},
            )
        )
        await db.commit()
        raise
    db.add(
        AuditLog(
            actor=user.actor,
            action="kafka.topic.deleted",
            resource_type="kafka_topic",
            resource_id=name,
            before_json=audit_base,
            after_json={"result": "succeeded"},
        )
    )
    await db.commit()
    return result


@router.get("/events")
async def events(
    cluster_id: UUID,
    topic: str,
    db: DB,
    user: Annotated[Principal, Depends(require("kafka.read"))],
    limit: int = Query(default=50, ge=1, le=200),
    partition: int | None = Query(default=None, ge=0),
    offset: int | None = Query(default=None, ge=0),
    operation: str | None = None,
    table: str | None = None,
    key: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
):
    if len(topic) > 249 or len(key or "") > 1000:
        raise DomainError("INVALID_FILTER", "Topic or key filter exceeds the allowed length", 422)
    cluster = await get(db, KafkaCluster, cluster_id)
    if event_slots.locked():
        raise DomainError("EXPLORER_BUSY", "Event exploration is busy; retry shortly", 429)
    async with event_slots:
        return await KafkaExplorer().events(
            cluster, topic, limit, partition, offset, operation, table, key, start_ms, end_ms
        )


@router.get("/data/schemas")
async def schemas(
    db: DB,
    user: Annotated[Principal, Depends(require("sources.read"))],
    source_id: UUID | None = None,
    limit: int = Query(default=500, ge=1, le=500),
    changes_only: bool = False,
):
    query = select(SchemaVersion).order_by(SchemaVersion.created_at.desc()).limit(limit)
    if source_id:
        query = query.where(SchemaVersion.source_id == source_id)
    if changes_only:
        query = query.where(SchemaVersion.version > 1)
    return [serialize(v) for v in (await db.scalars(query)).all()]


@router.get("/audit")
async def audit_logs(
    db: DB,
    user: Annotated[Principal, Depends(require("audit.read"))],
    resource_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    meaningful: bool = False,
    include_related: bool = False,
):
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if resource_id:
        query = query.where(
            or_(
                AuditLog.resource_id == resource_id,
                AuditLog.after_json["destination_id"].as_string() == resource_id,
            )
            if include_related
            else AuditLog.resource_id == resource_id
        )
    if meaningful:
        query = query.where(
            ~AuditLog.action.in_(
                [
                    "pipeline.state_observed",
                    "destination.state_observed",
                    "job.started",
                    "job.completed",
                ]
            )
        )
    return [serialize(a) for a in (await db.scalars(query)).all()]


@router.get("/operations/errors")
async def errors(
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.read"))],
    limit: int = Query(default=500, ge=1, le=500),
    active: bool = False,
):
    query = (
        select(PipelineEvent)
        .order_by(
            case(
                (PipelineEvent.status == "OPEN", 0),
                (PipelineEvent.status == "ACKNOWLEDGED", 1),
                else_=2,
            ),
            PipelineEvent.created_at.desc(),
        )
        .limit(limit)
    )
    if active:
        query = query.where(PipelineEvent.status.in_(["OPEN", "ACKNOWLEDGED"]))
    return [serialize(value) for value in (await db.scalars(query)).all()]


@router.patch("/operations/errors/{identifier}")
async def error_status(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
    status: str = Query(pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$"),
):
    error = await get(db, PipelineEvent, identifier)
    before = serialize(error)
    error.status = status
    audit(db, user.actor, "incident.updated", error, before)
    await db.commit()
    return serialize(error)


@router.get("/monitoring/overview")
async def monitoring(db: DB, user: Annotated[Principal, Depends(require("pipelines.read"))]):
    result: dict = {}
    for key, model in [
        ("sources", Source),
        ("pipelines", Pipeline),
        ("deliveries", PipelineDestination),
        ("kafka_clusters", KafkaCluster),
        ("connect_clusters", ConnectCluster),
        ("destinations", Destination),
    ]:
        result[key] = await db.scalar(select(func.count()).select_from(model))
    for state in ["RUNNING", "DEGRADED", "FAILED", "UNKNOWN", "PAUSED"]:
        result[state.lower()] = await db.scalar(
            select(func.count()).select_from(Pipeline).where(Pipeline.actual_state == state)
        )
        result["delivery_" + state.lower()] = await db.scalar(
            select(func.count())
            .select_from(PipelineDestination)
            .where(PipelineDestination.actual_state == state)
        )
        targets = (await db.scalars(select(Destination))).all()
        result["destination_" + state.lower()] = sum(
            [
                destination_service.aggregate(
                    [
                        link.actual_state
                        for link in await destination_service.deliveries(db, target.id)
                    ]
                )
                == state
                for target in targets
            ]
        )
    result["healthy_kafka_clusters"] = await db.scalar(
        select(func.count()).select_from(KafkaCluster).where(KafkaCluster.status == "HEALTHY")
    )
    result["healthy_connect_clusters"] = await db.scalar(
        select(func.count()).select_from(ConnectCluster).where(ConnectCluster.status == "HEALTHY")
    )
    result["healthy_sources"] = await db.scalar(
        select(func.count()).select_from(Source).where(Source.status == "HEALTHY")
    )
    result["errors"] = await db.scalar(
        select(func.count()).select_from(PipelineEvent).where(PipelineEvent.status == "OPEN")
    )
    result.update(
        throughput=None,
        cdc_lag=None,
        snapshots_running=None,
        metrics_notice="Stream metrics require a metrics provider; no values are estimated",
    )
    return result
