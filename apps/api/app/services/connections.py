import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.models.entities import Connection, Pipeline, PipelineDestination, SecretReference, now
from app.repositories.metadata import audit, serialize
from app.schemas.connections import ConnectionInput
from app.services.secrets import secret_provider

MASK = "************"


def view(connection: Connection) -> dict:
    result = serialize(connection)
    result["config"] = result.pop("config_json")
    result["capabilities"] = result.pop("capabilities_json")
    result["type"] = connection.provider
    result["last_checked"] = connection.last_tested_at
    result["credentials"] = {"configured": bool(connection.secret_ref), "masked_value": MASK}
    return result


def database_view(connection: Connection) -> dict:
    """Shape used by the deprecated /sources and /destinations APIs."""
    config = connection.config_json
    return {
        **serialize(connection),
        "type": connection.provider.lower(),
        "environment": str(config.get("environment", "DEV")),
        "host": str(config["host"]),
        "port": int(config["port"]),
        "database_name": str(config["database_name"]),
        "username": str(config["username"]),
        "ssl_enabled": bool(config.get("ssl_enabled", False)),
        "provider_options": dict(config.get("provider_options", {})),
        "last_health_check_at": connection.last_tested_at,
    }


def _capabilities(data: ConnectionInput) -> list[str]:
    if data.capabilities is not None:
        return list(dict.fromkeys(data.capabilities))
    return ["SOURCE", "DESTINATION"] if data.category == "DATABASE" else ["DESTINATION"]


def _credential_values(data: ConnectionInput) -> dict[str, str]:
    return {
        key: value.get_secret_value()
        for key, value in data.credentials.items()
        if value is not None and value.get_secret_value()
    }


def _required_credentials(data: ConnectionInput) -> set[str]:
    return {"password"} if data.category == "DATABASE" else {"access_key", "secret_key"}


def _validate_required_credentials(data: ConnectionInput, values: dict[str, str]) -> None:
    missing = _required_credentials(data) - set(values)
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


def transient(data: ConnectionInput) -> Connection:
    return Connection(
        name=data.name,
        category=data.category,
        provider=data.provider,
        description=data.description,
        config_json=data.config,
        capabilities_json=_capabilities(data),
    )


async def _run_test(connection: Connection, values: dict[str, str]) -> dict:
    if connection.category == "OBJECT_STORAGE":
        from app.services.object_storage import test_connection

        return await test_connection(connection, values)
    from app.providers import get_provider

    if "DESTINATION" in connection.capabilities_json:
        return (
            await get_provider(connection.type)
            .destination_adapter(connection, values["password"])
            .test_connection()
        )
    if "SOURCE" in connection.capabilities_json:
        return (
            await get_provider(connection.type)
            .source_adapter(connection, values["password"])
            .test()
        )
    raise DomainError(
        "DATABASE_CAPABILITY_REQUIRED",
        "Enable a source or destination capability before testing",
        422,
    )


async def test_input(data: ConnectionInput) -> dict:
    values = _credential_values(data)
    _validate_required_credentials(data, values)
    return await _run_test(transient(data), values)


async def create(session: AsyncSession, data: ConnectionInput, actor: str) -> Connection:
    values = _credential_values(data)
    _validate_required_credentials(data, values)
    connection = transient(data)
    connection.secret_ref = await secret_provider(session).put_secret(values)
    session.add(connection)
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
    requested = set(_capabilities(data))
    current = set(connection.capabilities_json)
    if connection.config_json != data.config and await dependencies(session, connection):
        raise DomainError(
            "CONNECTION_IN_USE",
            "Remove dependent captures and deliveries before changing endpoint settings",
            409,
        )
    if "SOURCE" in current - requested and await session.scalar(
        select(Pipeline.id).where(Pipeline.source_id == connection.id).limit(1)
    ):
        raise DomainError("CONNECTION_CAPABILITY_IN_USE", "The source capability is in use", 409)
    if "DESTINATION" in current - requested and await session.scalar(
        select(PipelineDestination.id)
        .where(PipelineDestination.destination_id == connection.id)
        .limit(1)
    ):
        raise DomainError(
            "CONNECTION_CAPABILITY_IN_USE", "The destination capability is in use", 409
        )
    before = view(connection)
    new_values = _credential_values(data)
    if new_values:
        combined = {**await credentials(session, connection), **new_values}
        _validate_required_credentials(data, combined)
        if connection.secret_ref:
            # Keep deployed ${cluecdc:ref:field} configs resolvable after rotation.
            await secret_provider(session).update_secret(connection.secret_ref, combined)
        else:
            connection.secret_ref = await secret_provider(session).put_secret(combined)
    else:
        _validate_required_credentials(data, await credentials(session, connection))
    connection.name = data.name
    connection.description = data.description
    connection.config_json = data.config
    connection.capabilities_json = list(requested)
    connection.status = "UNKNOWN"
    connection.last_test_status = None
    connection.last_test_message = None
    await session.flush()
    audit(session, actor, "connection.updated", connection, before)
    return connection


async def dependencies_for_connections(
    session: AsyncSession, identifiers: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict]]:
    result: dict[uuid.UUID, list[dict]] = defaultdict(list)
    if not identifiers:
        return result
    pipeline_rows = (
        await session.execute(
            select(Pipeline.source_id, Pipeline.id, Pipeline.name).where(
                Pipeline.source_id.in_(identifiers)
            )
        )
    ).all()
    for connection_id, pipeline_id, name in pipeline_rows:
        result[connection_id].append({"type": "pipeline", "id": str(pipeline_id), "name": name})
    delivery_rows = (
        await session.execute(
            select(
                PipelineDestination.destination_id,
                PipelineDestination.id,
                PipelineDestination.name,
                Pipeline.name,
            )
            .join(Pipeline, Pipeline.id == PipelineDestination.pipeline_id)
            .where(PipelineDestination.destination_id.in_(identifiers))
        )
    ).all()
    for connection_id, delivery_id, name, pipeline_name in delivery_rows:
        result[connection_id].append(
            {
                "type": "delivery",
                "id": str(delivery_id),
                "name": name,
                "pipeline": pipeline_name,
            }
        )
    return result


async def dependencies(session: AsyncSession, connection: Connection) -> list[dict]:
    return (await dependencies_for_connections(session, [connection.id])).get(connection.id, [])


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
    await session.delete(connection)
    await session.flush()
    if secret:
        await session.delete(secret)
    return {"deleted": True}


async def test(session: AsyncSession, connection: Connection, actor: str) -> dict:
    values = await credentials(session, connection)
    await session.commit()
    try:
        required = (
            {"password"}
            if connection.category == "DATABASE"
            else {
                "access_key",
                "secret_key",
            }
        )
        if required - set(values):
            raise DomainError(
                "CONNECTION_CREDENTIALS_REQUIRED",
                "Required connection credentials are missing",
                422,
            )
        result = await _run_test(connection, values)
    except DomainError as error:
        connection.status = "ERROR"
        connection.last_tested_at = now()
        connection.last_test_status = "ERROR"
        connection.last_test_message = error.message
        audit(session, actor, "connection.tested", connection)
        raise
    connection.status = "HEALTHY"
    connection.last_tested_at = now()
    connection.last_test_status = "HEALTHY"
    connection.last_test_message = "All connection checks passed"
    audit(session, actor, "connection.tested", connection)
    return result
