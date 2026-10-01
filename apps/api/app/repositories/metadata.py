from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, redact
from app.models.entities import AuditLog


def serialize(entity: Any) -> dict:
    return {
        col.key: getattr(entity, col.key)
        for col in inspect(entity).mapper.column_attrs
        if col.key not in {"ciphertext", "secret_ref", "config_encrypted"}
    }


async def get(session: AsyncSession, model: Any, identifier: UUID) -> Any:
    entity = await session.get(model, identifier)
    if entity is None:
        raise DomainError("NOT_FOUND", f"{model.__name__} was not found", 404)
    return entity


async def listing(session: AsyncSession, model: Any, limit: int = 500) -> list[dict]:
    entities = (
        await session.scalars(select(model).order_by(model.created_at.desc()).limit(limit))
    ).all()
    return [serialize(entity) for entity in entities]


def audit(session: AsyncSession, actor: str, action: str, entity: Any, before: dict | None = None):
    # Serialize JSON-safe snapshots without credentials.
    import json

    after = json.loads(json.dumps(redact(serialize(entity)), default=str))
    before_safe = json.loads(json.dumps(redact(before), default=str)) if before else None
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            resource_type=entity.__tablename__,
            resource_id=str(entity.id),
            before_json=before_safe,
            after_json=after,
        )
    )
