from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.adapters.postgres import PostgresAdapter
from app.core.errors import DomainError
from app.models.entities import Source


@pytest.mark.parametrize(
    "error,code",
    [
        (asyncpg.InvalidPasswordError("private"), "SOURCE_AUTH_FAILED"),
        (OSError("private"), "SOURCE_UNAVAILABLE"),
    ],
)
async def test_source_adapter_failure_codes(monkeypatch, error, code):
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(side_effect=error))
    source = Source(
        host="source", port=5432, database_name="commerce", username="cdc", ssl_enabled=False
    )
    with pytest.raises(DomainError) as exc:
        await PostgresAdapter(source, "private").test()
    assert exc.value.code == code
    assert "private" not in exc.value.message


async def test_readiness_warns_but_does_not_block_a_table_without_primary_key(monkeypatch):
    class Connection:
        async def fetchrow(self, query, *args):
            return {
                "oid": 42,
                "relreplident": b"f",
                "estimated_rows": 12,
                "estimated_size_bytes": 8192,
                "owns_table": True,
                "can_select": True,
            }

        async def fetch(self, query, *args):
            if "i.indisprimary" in query:
                return []
            return [{"name": "event_id", "type": "bigint", "nullable": False, "ordinal": 1}]

    @asynccontextmanager
    async def connection():
        yield Connection()

    source = Source(
        host="source", port=5432, database_name="commerce", username="cdc", ssl_enabled=False
    )
    adapter = PostgresAdapter(source, "private")
    monkeypatch.setattr(adapter, "connection", connection)

    readiness = await adapter.inspect_table("public", "events")

    assert readiness["ready"] is True
    assert readiness["checks"][1] == {"name": "primary_key", "status": "warning"}
    assert readiness["warnings"][0]["code"] == "NO_PRIMARY_KEY"
