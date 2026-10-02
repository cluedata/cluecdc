import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.models.entities import (
    Connection,
    Destination,
    Pipeline,
    PipelineDestination,
    SecretReference,
    Source,
    now,
)
from app.repositories.metadata import audit, get, serialize
from app.schemas.connections import ConnectionInput
from app.services.secrets import secret_provider

MASK = "••••••••••••"


def view(connection: Connection) -> dict:
    result = serialize(connection)
    result["config"] = result.pop("config_json")
    result["capabilities"] = result.pop("capabilities_json")
    result["type"] = connection.provider
    result["last_checked"] = connection.last_tested_at
    result["credentials"] = {"configured": bool(connection.secret_ref), "masked_value": MASK}
    return result


def _capabilities(data: ConnectionInput) -> list[str]:
    if data.capabilities is not None:
        return list(dict.fromkeys(data.capabilities))
    return ["SOURCE", "DESTINATION"]


def _database_values(connection: Connection) -> dict:
    config = connection.config_json
    return {
        "id": connection.id,
        "name": connection.name,
        "type": connection.provider.lower(),
        "environment": str(config.get("environment", "DEV")),
        "host": str(config["host"]),
        "port": int(config["port"]),
        "database_name": str(config["database_name"]),
        "username": str(config["username"]),
        "secret_ref": connection.secret_ref,
        "ssl_enabled": bool(config.get("ssl_enabled", False)),
        "provider_options": dict(config.get("provider_options", {})),
        "status": connection.status,
        "last_health_check_at": connection.last_tested_at,
    }


async def _sync_database_adapters(session: AsyncSession, connection: Connection) -> None:
    """Keep runtime-only legacy rows aligned with a canonical database Connection."""
    if connection.category != "DATABASE" or connection.provider not in {"POSTGRESQL", "MYSQL"}:
        return
    values = _database_values(connection)
    wanted = set(connection.capabilities_json)
    for capability, model in (("SOURCE", Source), ("DESTINATION", Destination)):
        adapter = await session.get(model, connection.id)
        if capability not in wanted:
            if adapter is not None:
                in_use = (
                    await session.scalar(
                        select(Pipeline.id).where(Pipeline.source_id == connection.id).limit(1)
                    )
                    if model is Source
                    else await session.scalar(
                        select(PipelineDestination.id)
                        .where(PipelineDestination.destination_id == connection.id)
                        .limit(1)
                    )
                )
                if in_use:
                    raise DomainError(
                        "CONNECTION_CAPABILITY_IN_USE",
                        f"The {capability.lower()} capability is in use",
                        409,
                    )
                await session.delete(adapter)
            continue
        if adapter is None:
            session.add(model(**values))
        else:
            for key, value in values.items():
                if key != "id":
                    setattr(adapter, key, value)


def _credential_values(data: ConnectionInput) -> dict[str, str]:
    return {
        key: value.get_secret_value()
        for key, value in data.credentials.items()
        if value is not None and value.get_secret_value()
    }


def _validate_required_credentials(data: ConnectionInput, values: dict[str, str]) -> None:
    required = {"password"}
    missing = required - set(values)
    if missing:
        raise DomainError(
            "CONNECTION_CREDENTIALS_REQUIRED",
            "Required connection credentials are missing",
            422,
            {"fields": sorted(missing)},
        )


async def credentials(session: AsyncSession, connection: Connection) -> dict[str, str]:
    if not connection.secret_ref:
        return {}
    return await secret_provider(session).get_secret(connection.secret_ref)


async def test_input(data: ConnectionInput) -> dict:
    """Test an unsaved connection without persisting credentials or configuration."""
    values = _credential_values(data)
    _validate_required_credentials(data, values)
    from app.providers import get_provider

    database = Destination(
        name=data.name,
        description=data.description,
        type=data.provider.lower(),
        environment=str(data.config.get("environment", "DEV")),
        host=str(data.config["host"]),
        port=int(data.config["port"]),
        database_name=str(data.config["database_name"]),
        username=str(data.config["username"]),
        secret_ref=uuid.uuid4(),
        ssl_enabled=bool(data.config.get("ssl_enabled", False)),
        provider_options=dict(data.config.get("provider_options", {})),
    )
    return (
        await get_provider(database.type)
        .destination_adapter(database, values["password"])
        .test_connection()
    )


async def create(session: AsyncSession, data: ConnectionInput, actor: str) -> Connection:
    values = _credential_values(data)
    _validate_required_credentials(data, values)
    secret_ref = await secret_provider(session).put_secret(values) if values else None
    connection = Connection(
        name=data.name,
        category=data.category,
        provider=data.provider,
        description=data.description,
        config_json=data.config,
        capabilities_json=_capabilities(data),
        secret_ref=secret_ref,
    )
    session.add(connection)
    await session.flush()
    await _sync_database_adapters(session, connection)
    await session.flush()
    audit(session, actor, "connection.created", connection)
    return connection


async def update(
    session: AsyncSession, connection: Connection, data: ConnectionInput, actor: str
) -> Connection:
    if connection.category != data.category or connection.provider != data.provider:
        raise DomainError(
            "CONNECTION_PROVIDER_IMMUTABLE",
            "Duplicate the connection to change its category or provider",
            422,
        )
    before = view(connection)
    new_values = _credential_values(data)
    old_secret: SecretReference | None = None
    if new_values:
        combined = {**await credentials(session, connection), **new_values}
        _validate_required_credentials(data, combined)
        old_ref = connection.secret_ref
        connection.secret_ref = await secret_provider(session).put_secret(combined)
        if old_ref:
            old_secret = await get(session, SecretReference, old_ref)
    else:
        _validate_required_credentials(data, await credentials(session, connection))
    connection.name = data.name
    connection.description = data.description
    connection.config_json = data.config
    connection.capabilities_json = _capabilities(data)
    connection.status = "UNKNOWN"
    connection.last_test_status = None
    connection.last_test_message = None
    await _sync_database_adapters(session, connection)
    await session.flush()
    if old_secret:
        await session.delete(old_secret)
    audit(session, actor, "connection.updated", connection, before)
    return connection


async def dependencies(session: AsyncSession, connection: Connection) -> list[dict]:
    result: list[dict[str, object]] = []
    if "SOURCE" in connection.capabilities_json:
        for pipeline in (
            await session.scalars(select(Pipeline).where(Pipeline.source_id == connection.id))
        ).all():
            result.append({"type": "pipeline", "id": str(pipeline.id), "name": pipeline.name})
    if "DESTINATION" in connection.capabilities_json:
        for link in (
            await session.scalars(
                select(PipelineDestination).where(
                    PipelineDestination.destination_id == connection.id
                )
            )
        ).all():
            delivery_pipeline = await session.get(Pipeline, link.pipeline_id)
            result.append(
                {
                    "type": "delivery",
                    "id": str(link.id),
                    "name": link.name,
                    "pipeline": delivery_pipeline.name if delivery_pipeline else None,
                }
            )
    return result


async def delete(session: AsyncSession, connection: Connection, actor: str) -> dict:
    used_by = await dependencies(session, connection)
    if used_by:
        raise DomainError(
            "CONNECTION_IN_USE",
            "Cannot delete this connection because it is in use",
            409,
            {"used_by": used_by},
        )
    secret = (
        await session.get(SecretReference, connection.secret_ref) if connection.secret_ref else None
    )
    audit(session, actor, "connection.deleted", connection)
    for model in (Source, Destination):
        adapter = await session.get(model, connection.id)
        if adapter is not None:
            await session.delete(adapter)
    await session.delete(connection)
    await session.flush()
    if secret:
        await session.delete(secret)
    return {"deleted": True}


async def test(session: AsyncSession, connection: Connection, actor: str) -> dict:
    values = await credentials(session, connection)
    try:
        destination = await session.get(Destination, connection.id)
        source = await session.get(Source, connection.id)
        from app.providers import get_provider

        database_provider = get_provider(connection.provider.lower())
        if destination is not None:
            result = await database_provider.destination_adapter(
                destination, values.get("password", "")
            ).test_connection()
        elif source is not None:
            result = await database_provider.source_adapter(
                source, values.get("password", "")
            ).test()
        else:
            raise DomainError(
                "DATABASE_CAPABILITY_REQUIRED",
                "Enable a source or destination capability before testing",
                422,
            )
    except DomainError as error:
        connection.status = "ERROR"
        connection.last_tested_at = now()
        connection.last_test_status = "ERROR"
        connection.last_test_message = error.message
        await _sync_database_adapters(session, connection)
        audit(session, actor, "connection.tested", connection)
        raise
    connection.status = "HEALTHY"
    connection.last_tested_at = now()
    connection.last_test_status = "HEALTHY"
    connection.last_test_message = "All connection checks passed"
    await _sync_database_adapters(session, connection)
    audit(session, actor, "connection.tested", connection)
    return result
