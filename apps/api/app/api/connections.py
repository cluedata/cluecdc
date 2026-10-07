from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require
from app.core.database import session_dependency
from app.core.errors import DomainError
from app.models.entities import Connection
from app.repositories.metadata import get
from app.schemas.connections import ConnectionInput
from app.services import connections as connection_service

router = APIRouter(prefix="/api/v1", tags=["Connections"])
DB = Annotated[AsyncSession, Depends(session_dependency)]
Read = Annotated[Principal, Depends(require("destinations.read"))]
Write = Annotated[Principal, Depends(require("destinations.write"))]
Admin = Annotated[Principal, Depends(require("destinations.admin"))]


@router.get("/connection-providers")
async def connection_providers(user: Read):
    return [
        {
            "provider": "POSTGRESQL",
            "category": "DATABASE",
            "name": "PostgreSQL",
            "description": "PostgreSQL CDC source and JDBC destination",
        },
        {
            "provider": "MYSQL",
            "category": "DATABASE",
            "name": "MySQL",
            "description": "MySQL CDC source and JDBC destination",
        },
        {
            "provider": "AWS_S3",
            "category": "OBJECT_STORAGE",
            "name": "AWS S3",
            "description": "AWS object storage sink destination",
            "capabilities": ["DESTINATION"],
            "delivery_type": "OBJECT_STORAGE",
        },
        {
            "provider": "MINIO",
            "category": "OBJECT_STORAGE",
            "name": "MinIO",
            "description": "S3-compatible object storage sink destination",
            "capabilities": ["DESTINATION"],
            "delivery_type": "OBJECT_STORAGE",
        },
    ]


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
    items = [
        item
        for item in (await db.scalars(query)).all()
        if not capability or capability.upper() in item.capabilities_json
    ]
    dependency_map = await connection_service.dependencies_for_connections(
        db, [item.id for item in items]
    )
    values = []
    for item in items:
        value = connection_service.view(item)
        used_by = dependency_map.get(item.id, [])
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
async def delete_connection(identifier: UUID, db: DB, user: Admin):
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
