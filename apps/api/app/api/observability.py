from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require
from app.core.database import session_dependency
from app.models.entities import Pipeline, now
from app.repositories.metadata import get
from app.schemas.observability import Capabilities, History, Observation
from app.services import observability as service

router = APIRouter(prefix="/api/v1")
DB = Annotated[AsyncSession, Depends(session_dependency)]
Reader = Annotated[Principal, Depends(require("pipelines.read"))]


@router.get("/observability/summary", response_model=list[Observation])
async def summary(
    db: DB,
    user: Reader,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    provider: str | None = None,
    status: str | None = None,
):
    # Cached metadata only. Pagination is stable; no GET invokes an external provider.
    from app.models.entities import Connection

    query = select(Pipeline).order_by(Pipeline.id).offset(offset).limit(limit)
    if provider:
        query = query.join(Connection, Connection.id == Pipeline.source_id).where(
            Connection.provider == provider.upper()
        )
    if status:
        query = query.where(Pipeline.actual_state == status)
    pipelines = (await db.scalars(query)).all()
    return [await service.latest(db, p) for p in pipelines]


@router.get("/pipelines/{identifier}/observability", response_model=Observation)
async def detail(identifier: UUID, db: DB, user: Reader):
    return await service.latest(db, await get(db, Pipeline, identifier))


@router.get("/pipelines/{identifier}/observability/history", response_model=History)
async def history(
    identifier: UUID,
    db: DB,
    user: Reader,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=500, ge=1, le=500),
):
    await get(db, Pipeline, identifier)
    end = end or now()
    start = start or end - timedelta(hours=1)
    return await service.history(db, identifier, start, end, limit)


@router.get("/pipelines/{identifier}/observability/capabilities", response_model=Capabilities)
async def capabilities(identifier: UUID, db: DB, user: Reader):
    return await service.capabilities(db, await get(db, Pipeline, identifier))
