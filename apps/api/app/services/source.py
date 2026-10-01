import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.monitoring import observe_database_connection
from app.models.entities import SchemaVersion, Source, SourceTable, now
from app.providers import get_provider
from app.repositories.metadata import audit, get
from app.services.secrets import secret_provider


async def adapter(session: AsyncSession, source: Source):
    credentials = await secret_provider(session).get_secret(source.secret_ref)
    return get_provider(source.type).source_adapter(source, credentials["password"])


def schema_diff(previous: dict, current: dict) -> list[dict]:
    old = {c["name"]: c for c in previous.get("columns", [])}
    new = {c["name"]: c for c in current.get("columns", [])}
    changes = []
    for name in sorted(old.keys() - new.keys()):
        changes.append({"column": name, "change": "column_removed", "classification": "BREAKING"})
    for name in sorted(new.keys() - old.keys()):
        changes.append(
            {
                "column": name,
                "change": "column_added",
                "classification": "NON_BREAKING"
                if new[name]["nullable"]
                else "POTENTIALLY_BREAKING",
            }
        )
    for name in sorted(old.keys() & new.keys()):
        if old[name]["type"] != new[name]["type"]:
            changes.append(
                {
                    "column": name,
                    "change": "type_changed",
                    "before": old[name]["type"],
                    "after": new[name]["type"],
                    "classification": "POTENTIALLY_BREAKING",
                }
            )
        if old[name]["nullable"] != new[name]["nullable"]:
            changes.append(
                {
                    "column": name,
                    "change": "nullable_changed",
                    "classification": "BREAKING" if not new[name]["nullable"] else "NON_BREAKING",
                }
            )
    if previous.get("primary_key") != current.get("primary_key"):
        changes.append({"change": "primary_key_changed", "classification": "BREAKING"})
    return changes


async def health(session: AsyncSession, source_id: UUID, actor: str) -> dict:
    source = await get(session, Source, source_id)
    from app.core.errors import DomainError

    try:
        result = await (await adapter(session, source)).readiness()
        source.status = "HEALTHY" if result.get("status") != "failed" else "UNHEALTHY"
    except DomainError as error:
        source.status = "UNHEALTHY"
        source.last_health_check_at = now()
        await observe_database_connection(
            session,
            kind="source",
            identifier=str(source.id),
            name=source.name,
            database_type=source.type,
            firing=True,
            message=error.message,
        )
        audit(session, actor, "source.health_failed", source)
        await session.commit()
        raise
    source.last_health_check_at = now()
    await observe_database_connection(
        session,
        kind="source",
        identifier=str(source.id),
        name=source.name,
        database_type=source.type,
        firing=False,
        message="Source database connection recovered",
    )
    audit(session, actor, "source.health_checked", source)
    return result


async def discover(session: AsyncSession, source_id: UUID, actor: str) -> dict:
    # Row lock serializes discovery/schema-version writes for this source.
    source = await session.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        from app.core.errors import DomainError

        raise DomainError("NOT_FOUND", "Source was not found", 404)
    tables = await (await adapter(session, source)).discover()
    existing = {
        (t.schema_name, t.table_name): t
        for t in (
            await session.scalars(select(SourceTable).where(SourceTable.source_id == source_id))
        ).all()
    }
    for data in tables:
        key = (data["schema_name"], data["table_name"])
        entity = existing.pop(key, None)
        if entity is None:
            entity = SourceTable(source_id=source_id, **data)
            session.add(entity)
        else:
            for k, v in data.items():
                setattr(entity, k, v)
        schema = {"columns": data["columns_json"], "primary_key": data["primary_key_columns"]}
        digest = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
        previous = await session.scalar(
            select(SchemaVersion)
            .where(
                SchemaVersion.source_id == source_id,
                SchemaVersion.schema_name == key[0],
                SchemaVersion.table_name == key[1],
            )
            .order_by(SchemaVersion.version.desc())
            .limit(1)
        )
        if previous is None or previous.schema_hash != digest:
            session.add(
                SchemaVersion(
                    source_id=source_id,
                    schema_name=key[0],
                    table_name=key[1],
                    version=previous.version + 1 if previous else 1,
                    schema_json=schema,
                    schema_hash=digest,
                    diff_json=schema_diff(previous.schema_json, schema) if previous else [],
                )
            )
    for dropped in existing.values():
        await session.delete(dropped)
    source.status = "HEALTHY"
    source.last_health_check_at = now()
    audit(session, actor, "source.discovered", source)
    return {"tables_discovered": len(tables), "limit": 500}
