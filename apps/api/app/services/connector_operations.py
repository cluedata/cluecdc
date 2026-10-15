import uuid
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import DomainError
from app.models.entities import Connector, now


async def claim(
    session: AsyncSession, connector_id: uuid.UUID, operation: str
) -> tuple[str, Connector]:
    connector = await session.scalar(
        select(Connector).where(Connector.id == connector_id).with_for_update()
    )
    if connector is None:
        raise DomainError("NOT_FOUND", "Connector metadata was not found", 404)
    timestamp = now()
    active_after = timestamp - timedelta(seconds=get_settings().connector_operation_lease_seconds)
    started_at = connector.operation_started_at
    if started_at is not None and started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    if connector.operation_token and started_at and started_at >= active_after:
        raise DomainError(
            "CONNECTOR_OPERATION_ACTIVE",
            "Another connector operation is still active; retry after it completes",
            409,
            {"operation": connector.operation_name},
        )
    token = uuid.uuid4().hex
    connector.operation_token = token
    connector.operation_name = operation
    connector.operation_started_at = timestamp
    await session.commit()
    return token, connector


async def owned(session: AsyncSession, connector_id: uuid.UUID, token: str) -> Connector:
    connector = await session.scalar(
        select(Connector).where(Connector.id == connector_id).with_for_update()
    )
    if connector is None:
        raise DomainError("NOT_FOUND", "Connector metadata was not found", 404)
    if connector.operation_token != token:
        raise DomainError(
            "CONNECTOR_OPERATION_LOST",
            "Connector operation ownership expired; refresh runtime state before retrying",
            409,
        )
    return connector


def complete(connector: Connector, token: str) -> None:
    if connector.operation_token != token:
        raise DomainError("CONNECTOR_OPERATION_LOST", "Connector operation ownership was lost", 409)
    connector.operation_token = None
    connector.operation_name = None
    connector.operation_started_at = None


async def release(session: AsyncSession, connector_id: uuid.UUID, token: str) -> None:
    connector = await session.scalar(
        select(Connector).where(Connector.id == connector_id).with_for_update()
    )
    if connector is not None and connector.operation_token == token:
        complete(connector, token)
    await session.commit()
