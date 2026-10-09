from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require
from app.core.database import session_dependency
from app.core.errors import DomainError
from app.models.entities import Connection, PipelineDestination
from app.repositories.metadata import get
from app.schemas.requests import DeliveryInput, DestinationInput
from app.services.destinations import service

router = APIRouter(prefix="/api/v1/destinations", tags=["Destinations"])
DB = Annotated[AsyncSession, Depends(session_dependency)]
Read = Annotated[Principal, Depends(require("destinations.read"))]
Write = Annotated[Principal, Depends(require("destinations.write"))]
Operate = Annotated[Principal, Depends(require("destinations.operate"))]
Admin = Annotated[Principal, Depends(require("destinations.admin"))]
DeliveryWrite = Annotated[Principal, Depends(require("deliveries.write"))]
DeliveryOperate = Annotated[Principal, Depends(require("deliveries.operate"))]
DeliveryAdmin = Annotated[Principal, Depends(require("deliveries.admin"))]


async def failure(
    db: AsyncSession, identifier: UUID, error: DomainError, pipeline_id: UUID | None = None
):
    await db.rollback()
    await service.record_error(db, identifier, error, pipeline_id)
    await db.commit()


@router.get("")
async def listing(db: DB, user: Read):
    values = (
        await db.scalars(select(Connection).order_by(Connection.created_at.desc()).limit(500))
    ).all()
    return await service.details(
        db, [value for value in values if "DESTINATION" in value.capabilities_json]
    )


@router.post("/test-connection")
async def test_connection(data: DestinationInput, db: DB, user: Write):
    return await service.test_unsaved(db, data)


@router.post("", status_code=201)
async def create(data: DestinationInput, db: DB, user: Write):
    value = await service.create(db, data, user.actor)
    await db.commit()
    return await service.detail(db, value)


@router.get("/{identifier}")
async def detail(identifier: UUID, db: DB, user: Read):
    value = await get(db, Connection, identifier)
    if "DESTINATION" not in value.capabilities_json:
        raise DomainError("NOT_FOUND", "Destination was not found", 404)
    return await service.detail(db, value)


@router.put("/{identifier}")
async def update(identifier: UUID, data: DestinationInput, db: DB, user: Write):
    value = await service.update(db, await service.locked(db, identifier), data, user.actor)
    await db.commit()
    return await service.detail(db, value)


@router.delete("/{identifier}")
async def delete(identifier: UUID, db: DB, user: Admin):
    result = await service.delete(db, await service.locked(db, identifier), user.actor)
    await db.commit()
    return result


@router.post("/{identifier}/test")
async def test(identifier: UUID, db: DB, user: Write):
    try:
        value = await get(db, Connection, identifier)
        if "DESTINATION" not in value.capabilities_json:
            raise DomainError("NOT_FOUND", "Destination was not found", 404)
        result = await service.test(db, value, user.actor)
        await db.commit()
        return result
    except DomainError as error:
        await failure(db, identifier, error)
        raise


@router.post("/{identifier}/preview")
async def preview(identifier: UUID, data: DeliveryInput, db: DB, user: DeliveryWrite):
    value = await get(db, Connection, identifier)
    if "DESTINATION" not in value.capabilities_json:
        raise DomainError("NOT_FOUND", "Destination was not found", 404)
    return await service.preview(db, value, data)


@router.post("/{identifier}/deploy", status_code=201)
async def deploy(identifier: UUID, data: DeliveryInput, db: DB, user: DeliveryWrite):
    try:
        value = await get(db, Connection, identifier)
        if "DESTINATION" not in value.capabilities_json:
            raise DomainError("NOT_FOUND", "Destination was not found", 404)
        return await service.deploy(db, value, data, user.actor)
    except DomainError as error:
        await failure(db, identifier, error, data.pipeline_id)
        raise


@router.get("/{identifier}/mappings")
async def mappings(identifier: UUID, db: DB, user: Read):
    value = await get(db, Connection, identifier)
    if "DESTINATION" not in value.capabilities_json:
        raise DomainError("NOT_FOUND", "Destination was not found", 404)
    return [
        {
            "delivery_id": link.id,
            "pipeline_id": link.pipeline_id,
            "mappings": link.topic_mapping_json,
            "configuration": link.configuration_json,
            "actual_state": link.actual_state,
        }
        for link in await service.deliveries(db, identifier)
    ]


@router.get("/{identifier}/status")
async def status(identifier: UUID, db: DB, user: Read):
    value = await get(db, Connection, identifier)
    if "DESTINATION" not in value.capabilities_json:
        raise DomainError("NOT_FOUND", "Destination was not found", 404)
    links = (
        await db.scalars(
            select(PipelineDestination)
            .where(PipelineDestination.destination_id == identifier)
            .order_by(PipelineDestination.created_at, PipelineDestination.id)
        )
    ).all()
    states = [await service.reconcile(db, link) for link in links]
    await db.commit()
    return {
        "desired_state": service.aggregate([state["desired_state"] for state in states]),
        "actual_state": service.aggregate([state["actual_state"] for state in states]),
        "deliveries": states,
    }


@router.put("/{identifier}/deliveries/{delivery_id}/mappings")
async def update_mappings(
    identifier: UUID, delivery_id: UUID, data: DeliveryInput, db: DB, user: DeliveryWrite
):
    try:
        destination = await get(db, Connection, identifier)
        link = await service.delivery_by_id(db, delivery_id)
        if link.destination_id != identifier:
            raise DomainError("NOT_FOUND", "Delivery was not found on this destination", 404)
        return await service.update_mapping(db, destination, link, data, user.actor)
    except DomainError as error:
        await failure(db, identifier, error)
        raise


@router.delete("/{identifier}/deliveries/{delivery_id}")
async def remove_delivery(identifier: UUID, delivery_id: UUID, db: DB, user: DeliveryAdmin):
    destination = await get(db, Connection, identifier)
    link = await service.delivery_by_id(db, delivery_id)
    if link.destination_id != identifier:
        raise DomainError("NOT_FOUND", "Delivery was not found on this destination", 404)
    return await service.operate(db, destination, link, "delete", user.actor)


@router.post("/{identifier}/deliveries/{delivery_id}/tasks/{task_id}/restart")
async def restart_task(
    identifier: UUID,
    delivery_id: UUID,
    task_id: int,
    db: DB,
    user: DeliveryOperate,
):
    if task_id < 0:
        raise DomainError("INVALID_TASK", "Task ID must be non-negative", 422)
    destination = await get(db, Connection, identifier)
    link = await service.delivery_by_id(db, delivery_id)
    if link.destination_id != identifier:
        raise DomainError("NOT_FOUND", "Delivery was not found on this destination", 404)
    return await service.operate(db, destination, link, "restart-task", user.actor, task_id)


@router.post("/{identifier}/{operation}")
async def operate(
    identifier: UUID,
    operation: str,
    db: DB,
    user: DeliveryOperate,
    delivery_id: UUID | None = Query(default=None),
):
    if operation not in {"pause", "resume", "restart"}:
        raise DomainError("INVALID_OPERATION", "Use pause, resume, or restart", 422)
    destination = await get(db, Connection, identifier)
    links = (
        [await service.delivery_by_id(db, delivery_id)]
        if delivery_id
        else await service.deliveries(db, identifier)
    )
    if not links:
        raise DomainError("NOT_DEPLOYED", "Add and deploy a delivery first", 409)
    if any(link.destination_id != identifier for link in links):
        raise DomainError("NOT_FOUND", "Delivery was not found on this destination", 404)
    completed = []
    for link in links:
        try:
            destination = await get(db, Connection, identifier)
            completed.append(await service.operate(db, destination, link, operation, user.actor))
        except DomainError as error:
            await failure(db, identifier, error, link.pipeline_id)
            raise DomainError(
                error.code, error.message, error.status, {"completed": completed}
            ) from error
    return {"deliveries": completed}
