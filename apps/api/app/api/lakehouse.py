from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connection_providers import provider_catalog
from app.core.auth import Principal, require
from app.core.database import session_dependency
from app.core.errors import DomainError
from app.models.entities import Connection, LakehouseTarget
from app.repositories.metadata import get
from app.schemas.lakehouse import (
    ConnectionInput,
    IcebergDeliveryInput,
    LakehouseTargetInput,
)
from app.services import connections as connection_service
from app.services import lakehouse as lakehouse_service

router = APIRouter(prefix="/api/v1", tags=["Lakehouse"])
DB = Annotated[AsyncSession, Depends(session_dependency)]
Read = Annotated[Principal, Depends(require("destinations.read"))]
Write = Annotated[Principal, Depends(require("destinations.write"))]
Operate = Annotated[Principal, Depends(require("destinations.operate"))]


@router.get("/connection-providers")
async def connection_providers(user: Read):
    return provider_catalog()


@router.get("/connections")
async def connections(
    db: DB,
    user: Read,
    category: str | None = Query(default=None),
    capability: str | None = Query(default=None),
):
    query = select(Connection).order_by(Connection.created_at.desc()).limit(500)
    if category:
        query = query.where(Connection.category == category.upper())
    values = []
    for item in (await db.scalars(query)).all():
        if capability and capability.upper() not in item.capabilities_json:
            continue
        value = connection_service.view(item)
        used_by = await connection_service.dependencies(db, item)
        value["used_by"] = used_by
        value["used_by_count"] = len({(entry["type"], entry["id"]) for entry in used_by})
        values.append(value)
    return values


@router.post("/connections", status_code=201)
async def create_connection(data: ConnectionInput, db: DB, user: Write):
    item = await connection_service.create(db, data, user.actor)
    await db.commit()
    return connection_service.view(item)


@router.post("/connections/test")
async def test_unsaved_connection(data: ConnectionInput, user: Write):
    return await connection_service.test_input(data)


@router.get("/connections/{identifier}")
async def connection_detail(identifier: UUID, db: DB, user: Read):
    item = await get(db, Connection, identifier)
    result = connection_service.view(item)
    result["used_by"] = await connection_service.dependencies(db, item)
    return result


@router.put("/connections/{identifier}")
async def update_connection(identifier: UUID, data: ConnectionInput, db: DB, user: Write):
    item = await connection_service.update(
        db, await get(db, Connection, identifier), data, user.actor
    )
    await db.commit()
    return connection_service.view(item)


@router.delete("/connections/{identifier}")
async def delete_connection(identifier: UUID, db: DB, user: Write):
    result = await connection_service.delete(db, await get(db, Connection, identifier), user.actor)
    await db.commit()
    return result


@router.post("/connections/{identifier}/test")
async def test_connection(identifier: UUID, db: DB, user: Write):
    item = await get(db, Connection, identifier)
    try:
        result = await connection_service.test(db, item, user.actor)
    except DomainError:
        await db.commit()
        raise
    await db.commit()
    return result


@router.get("/lakehouse-targets")
@router.get("/lakehouse-destinations", deprecated=True)
async def lakehouse_destinations(db: DB, user: Read):
    values = (
        await db.scalars(
            select(LakehouseTarget).order_by(LakehouseTarget.created_at.desc()).limit(500)
        )
    ).all()
    return [await lakehouse_service.detail(db, item) for item in values]


@router.post("/lakehouse-targets", status_code=201)
@router.post("/lakehouse-destinations", status_code=201, deprecated=True)
async def create_lakehouse_target(data: LakehouseTargetInput, db: DB, user: Write):
    item = await lakehouse_service.create(db, data, user.actor)
    await db.commit()
    return await lakehouse_service.detail(db, item)


@router.get("/lakehouse-targets/{identifier}")
@router.get("/lakehouse-destinations/{identifier}", deprecated=True)
async def lakehouse_destination(identifier: UUID, db: DB, user: Read):
    return await lakehouse_service.detail(db, await get(db, LakehouseTarget, identifier))


@router.put("/lakehouse-targets/{identifier}")
@router.put("/lakehouse-destinations/{identifier}", deprecated=True)
async def update_lakehouse_destination(
    identifier: UUID, data: LakehouseTargetInput, db: DB, user: Write
):
    item = await lakehouse_service.update(
        db, await get(db, LakehouseTarget, identifier), data, user.actor
    )
    await db.commit()
    return await lakehouse_service.detail(db, item)


@router.delete("/lakehouse-targets/{identifier}")
@router.delete("/lakehouse-destinations/{identifier}", deprecated=True)
async def delete_lakehouse_destination(identifier: UUID, db: DB, user: Write):
    result = await lakehouse_service.delete(
        db, await get(db, LakehouseTarget, identifier), user.actor
    )
    await db.commit()
    return result


@router.post("/lakehouse-targets/{identifier}/preview-delivery")
@router.post("/lakehouse-destinations/{identifier}/preview-delivery", deprecated=True)
async def preview_iceberg_delivery(
    identifier: UUID, data: IcebergDeliveryInput, db: DB, user: Write
):
    return await lakehouse_service.preview_delivery(
        db, await get(db, LakehouseTarget, identifier), data
    )


@router.post("/lakehouse-targets/{identifier}/deploy", status_code=201)
@router.post("/lakehouse-destinations/{identifier}/deploy", status_code=201, deprecated=True)
async def deploy_iceberg_delivery(
    identifier: UUID, data: IcebergDeliveryInput, db: DB, user: Operate
):
    return await lakehouse_service.deploy(
        db, await get(db, LakehouseTarget, identifier), data, user.actor
    )
