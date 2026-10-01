import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.models.entities import Pipeline, Source


async def assign_mysql_server_id(
    session: AsyncSession,
    source_id: uuid.UUID,
    requested: int | None = None,
    exclude_source_id: uuid.UUID | None = None,
) -> int:
    used = {
        int(source.provider_options["server_id"])
        for source in (await session.scalars(select(Source).where(Source.type == "mysql"))).all()
        if source.id != exclude_source_id and source.provider_options.get("server_id")
    }
    if requested is not None:
        if requested in used:
            raise DomainError(
                "SERVER_ID_CONFLICT",
                "This MySQL Debezium server ID is already assigned to another source",
                409,
            )
        return requested
    candidate = 540_000_000 + source_id.int % 100_000_000
    while candidate in used:
        candidate = 540_000_000 + ((candidate - 540_000_000 + 1) % 100_000_000)
    return candidate


async def assign_mysql_connector_server_id(
    session: AsyncSession, source: Source, pipeline_id: uuid.UUID
) -> int:
    sources = (await session.scalars(select(Source).where(Source.type == "mysql"))).all()
    pipelines = (await session.scalars(select(Pipeline))).all()
    used = {
        int(value)
        for value in [
            *(item.provider_options.get("server_id") for item in sources),
            *(
                item.config_options.get("provider_options", {}).get("server_id")
                for item in pipelines
            ),
        ]
        if value
    }
    base = int(source.provider_options["server_id"])
    candidate = 1 + ((base + pipeline_id.int % 1_000_003) % 4_294_967_294)
    while candidate in used:
        candidate = 1 + (candidate % 4_294_967_294)
    return candidate
