from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require
from app.core.database import session_dependency
from app.core.errors import DomainError
from app.models.entities import (
    Alert,
    Connection,
    DataQualityCheck,
    DataQualityRuleResult,
    DataQualityRun,
    NotificationChannel,
    now,
)
from app.repositories.metadata import audit, get, serialize
from app.schemas.data_quality import CheckInput, Dataset, RunInput
from app.services import data_quality as service
from app.services import data_quality_engine as engine
from app.services.data_quality_measurements import reader

router = APIRouter(prefix="/api/v1/data-quality", tags=["Data Quality"])
DB = Annotated[AsyncSession, Depends(session_dependency)]
Read = Annotated[Principal, Depends(require("data_quality.read"))]
Write = Annotated[Principal, Depends(require("data_quality.write"))]
Run = Annotated[Principal, Depends(require("data_quality.run"))]
Admin = Annotated[Principal, Depends(require("data_quality.admin"))]


@router.get("/rules/catalog")
async def catalog(user: Read):
    schema = CheckInput.model_json_schema()
    return {
        "rules": [
            {
                "type": key,
                "description": value,
                "providers": ["POSTGRESQL", "MYSQL"],
                "provider_pairs": [list(pair) for pair in sorted(engine.SUPPORTED_PAIRS)],
                "data_model": "CURRENT_STATE",
            }
            for key, value in engine.RULES.items()
        ],
        "configuration_schema": schema,
        "consistency": "BEST_EFFORT",
    }


@router.get("/connections")
async def connections(db: DB, user: Write):
    items = (await db.scalars(select(Connection).order_by(Connection.name).limit(500))).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "provider": item.provider,
            "database": item.database_name,
            "capabilities": item.capabilities_json,
            "supported": item.provider in {"POSTGRESQL", "MYSQL"},
            "reason": None
            if item.provider in {"POSTGRESQL", "MYSQL"}
            else "Only PostgreSQL/MySQL CURRENT_STATE tables are supported",
        }
        for item in items
    ]


@router.get("/channels")
async def channels(db: DB, user: Write):
    items = (await db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))).all()
    return [
        {"id": item.id, "name": item.name, "enabled": item.enabled, "type": item.type}
        for item in items
    ]


@router.get("/connections/{identifier}/tables")
async def tables(identifier: UUID, db: DB, user: Write):
    conn = await get(db, Connection, identifier)
    if conn.provider not in {"POSTGRESQL", "MYSQL"}:
        raise DomainError(
            "DQ_PROVIDER_UNSUPPORTED", "Provider is unsupported for Data Quality", 422
        )
    async with reader(db, conn) as connection:
        return {
            "database": conn.database_name,
            "tables": await connection.tables(conn.database_name),
        }


@router.post("/validate")
async def validate(data: CheckInput, db: DB, user: Write):
    try:
        _, _, capability = await engine.inspect(db, data, now())
        return capability
    except DomainError as exc:
        return {
            "status": "UNKNOWN" if exc.status >= 500 else "UNSUPPORTED",
            "rules": [
                {
                    "rule_type": r.type,
                    "status": "UNKNOWN" if exc.status >= 500 else "UNSUPPORTED",
                    "reasons": [exc.message],
                    "error_code": exc.code,
                }
                for r in data.rules
            ],
            "warnings": engine.LIMITATIONS,
        }


@router.post("/columns")
async def columns(data: Dataset, db: DB, user: Write):
    conn = await get(db, Connection, data.connection_id)
    if conn.provider not in {"POSTGRESQL", "MYSQL"} or data.database != conn.database_name:
        raise DomainError("DQ_DATABASE_SCOPE", "Select a supported registered database", 422)
    if conn.provider == "MYSQL" and data.schema_name != conn.database_name:
        raise DomainError("DQ_DATABASE_SCOPE", "MySQL schema must equal database", 422)
    async with reader(db, conn) as connection:
        return await connection.columns(data)


@router.get("/checks")
async def checks(
    db: DB,
    user: Read,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    source: UUID | None = None,
    destination: UUID | None = None,
    enabled: bool | None = None,
    rule: str | None = None,
    quality: str | None = None,
):
    query = select(DataQualityCheck).where(DataQualityCheck.deleted_at.is_(None))
    if source:
        query = query.where(DataQualityCheck.source_connection_id == source)
    if destination:
        query = query.where(DataQualityCheck.destination_connection_id == destination)
    if enabled is not None:
        query = query.where(DataQualityCheck.enabled == enabled)
    if rule:
        from app.models.entities import DataQualityRule

        query = query.where(
            DataQualityCheck.id.in_(
                select(DataQualityRule.check_id).where(DataQualityRule.rule_type == rule)
            )
        )
    if quality:
        latest = (
            select(DataQualityRun.quality)
            .where(DataQualityRun.check_id == DataQualityCheck.id)
            .order_by(DataQualityRun.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(latest == quality)
    items = (
        await db.scalars(
            query.order_by(DataQualityCheck.created_at.desc()).offset(offset).limit(limit)
        )
    ).all()
    return {
        "items": [await service.view(db, item) for item in items],
        "limit": limit,
        "offset": offset,
        "has_more": len(items) == limit,
    }


@router.post("/checks", status_code=201)
async def create(data: CheckInput, db: DB, user: Write):
    item = await service.save(db, data, user.actor)
    await db.commit()
    return await service.view(db, item)


@router.get("/checks/{identifier}")
async def detail(identifier: UUID, db: DB, user: Read):
    item = await service.check(db, identifier)
    result = await service.view(db, item)
    alerts = (
        await db.scalars(
            select(Alert)
            .where(Alert.source_type == "data_quality", Alert.source_id.like(f"{item.id}:%"))
            .order_by(Alert.created_at.desc())
            .limit(50)
        )
    ).all()
    result["alerts"] = [serialize(a) for a in alerts]
    return result


@router.patch("/checks/{identifier}")
async def update_check(identifier: UUID, data: dict, db: DB, user: Write):
    item = await db.scalar(
        select(DataQualityCheck)
        .where(DataQualityCheck.id == identifier, DataQualityCheck.deleted_at.is_(None))
        .with_for_update()
    )
    if not item:
        raise DomainError("NOT_FOUND", "Data Quality check was not found", 404)
    from pydantic import ValidationError

    try:
        config = CheckInput.model_validate({**item.config_json, **data})
    except ValidationError as exc:
        raise DomainError(
            "DQ_INVALID_CONFIGURATION",
            "Invalid check configuration",
            422,
            {"fields": [list(e["loc"]) for e in exc.errors()]},
        ) from exc
    item = await service.save(db, config, user.actor, item)
    await db.commit()
    return await service.view(db, item)


@router.delete("/checks/{identifier}")
async def delete_check(identifier: UUID, db: DB, user: Admin):
    item = await service.check(db, identifier)
    item.deleted_at, item.next_run_at, item.enabled = now(), None, False
    audit(db, user.actor, "data_quality.deleted", item)
    await db.commit()
    return {"id": identifier, "deleted": True}


@router.post("/checks/{identifier}/run", status_code=202)
async def run(identifier: UUID, db: DB, user: Run, data: RunInput = RunInput()):
    item = await service.check(db, identifier)
    result = await service.enqueue(db, item, user.actor, request_key=data.request_key)
    await db.commit()
    return serialize(result)


async def toggle(db, identifier, actor, enabled):
    item = await service.check(db, identifier)
    await db.refresh(item, with_for_update=True)
    config = CheckInput.model_validate(item.config_json)
    if enabled and config.comparison.period == "FIXED":
        raise DomainError("DQ_FIXED_MANUAL_ONLY", "Fixed windows cannot be scheduled", 422)
    config.schedule.enabled = enabled
    if (
        enabled
        and config.schedule.frequency_seconds == 60
        and config.comparison.scope == "FULL_TABLE"
        and any(r.type in {"row_count", "row_count_diff"} for r in config.rules)
        and not config.schedule.full_scan_acknowledged
    ):
        raise DomainError(
            "DQ_SCAN_ACK_REQUIRED", "Acknowledge one-minute full-count cost before resuming", 422
        )
    item.config_json = config.model_dump(mode="json")
    item.version += 1
    item.next_run_at = (
        now() + timedelta(seconds=config.schedule.frequency_seconds)
        if enabled and item.enabled
        else None
    )
    item.updated_by = actor
    audit(db, actor, "data_quality.resumed" if enabled else "data_quality.paused", item)
    await db.commit()
    return await service.view(db, item)


@router.post("/checks/{identifier}/pause")
async def pause(identifier: UUID, db: DB, user: Write):
    return await toggle(db, identifier, user.actor, False)


@router.post("/checks/{identifier}/resume")
async def resume(identifier: UUID, db: DB, user: Write):
    return await toggle(db, identifier, user.actor, True)


@router.get("/checks/{identifier}/runs")
async def history(
    identifier: UUID,
    db: DB,
    user: Read,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    await service.check(db, identifier)
    items = (
        await db.scalars(
            select(DataQualityRun)
            .where(DataQualityRun.check_id == identifier)
            .order_by(DataQualityRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [serialize(i) for i in items],
        "limit": limit,
        "offset": offset,
        "has_more": len(items) == limit,
    }


@router.get("/runs/{identifier}")
async def run_detail(identifier: UUID, db: DB, user: Read):
    item = await get(db, DataQualityRun, identifier)
    result = serialize(item)
    result["results"] = await results(identifier, db, user)
    return result


@router.get("/runs/{identifier}/results")
async def results(identifier: UUID, db: DB, user: Read):
    await get(db, DataQualityRun, identifier)
    items = (
        await db.scalars(
            select(DataQualityRuleResult)
            .where(DataQualityRuleResult.run_id == identifier)
            .order_by(DataQualityRuleResult.created_at)
        )
    ).all()
    return [serialize(i) for i in items]
