from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

import asyncpg

from app.core.config import get_settings
from app.core.errors import DomainError
from app.models.entities import Source


class SourceAdapter(Protocol):
    async def test(self) -> dict: ...
    async def readiness(self) -> dict: ...
    async def discover(self) -> list[dict]: ...
    async def inspect_table(self, schema_name: str, table_name: str) -> dict: ...


class PostgresAdapter:
    def __init__(self, source: Source, password: str):
        self.source = source
        self.password = password

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        connection = None
        try:
            connection = await asyncpg.connect(
                host=self.source.host,
                port=self.source.port,
                database=self.source.database_name,
                user=self.source.username,
                password=self.password,
                ssl="verify-full" if self.source.ssl_enabled else False,
                timeout=get_settings().integration_timeout_seconds,
                command_timeout=30,
            )
            yield connection
        except asyncpg.InvalidPasswordError as exc:
            raise DomainError(
                "SOURCE_AUTH_FAILED", "Source rejected the supplied credentials", 422
            ) from exc
        except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
            raise DomainError(
                "SOURCE_UNAVAILABLE", "Source connection or read-only query failed", 503
            ) from exc
        finally:
            if connection:
                await connection.close()

    async def test(self) -> dict:
        async with self.connection() as conn:
            return {"status": "HEALTHY", "version": await conn.fetchval("SELECT version()")}

    async def readiness(self) -> dict:
        async with self.connection() as conn:
            checks: list[dict] = []

            def check(key, current, expected, passed, action, severity="error"):
                checks.append(
                    {
                        "key": key,
                        "name": key.replace("_", " ").title(),
                        "status": "passed"
                        if passed
                        else "warning"
                        if severity == "warning"
                        else "failed",
                        "severity": severity,
                        "description": f"Verify PostgreSQL {key}",
                        "current_value": current,
                        "expected_value": expected,
                        "recommended_action": None if passed else action,
                    }
                )

            check("connectivity", True, True, True, "Check network and credentials")
            version = int(await conn.fetchval("SHOW server_version_num"))
            check("server_version", version, ">= 130000", version >= 130000, "Upgrade PostgreSQL")
            for key, expected in [
                ("wal_level", "logical"),
                ("max_replication_slots", "> 0"),
                ("max_wal_senders", "> 0"),
            ]:
                value = await conn.fetchval(f"SHOW {key}")
                passed = value == "logical" if key == "wal_level" else int(value) > 0
                check(
                    key,
                    value,
                    expected,
                    passed,
                    f"Set {key} in PostgreSQL configuration and restart",
                )
            replication = await conn.fetchval(
                "SELECT rolreplication OR rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            check(
                "replication_privilege",
                replication,
                True,
                replication,
                "Ask an administrator to grant REPLICATION",
            )
            slots = await conn.fetchval("SELECT count(*) FROM pg_replication_slots")
            max_slots = int(await conn.fetchval("SHOW max_replication_slots"))
            check(
                "available_replication_slots",
                max_slots - slots,
                "> 0",
                slots < max_slots,
                "Increase replication slot capacity",
                "warning",
            )
            can_publish = await conn.fetchval(
                "SELECT has_database_privilege(current_database(), 'CREATE')"
            )
            check(
                "publication_readiness",
                can_publish,
                True,
                can_publish,
                "Grant CREATE on the database and ownership of selected tables, "
                "or pre-create the publication",
                "warning",
            )
            checks.append(
                {
                    "key": "publication_table_ownership",
                    "name": "Publication table ownership",
                    "status": "warning",
                    "severity": "warning",
                    "description": "Filtered publication creation requires ownership "
                    "of every selected table",
                    "current_value": "Checked at table selection",
                    "expected_value": "Table ownership",
                    "recommended_action": "Use a table-owning replication role "
                    "or pre-create a publication",
                }
            )
            status = (
                "failed"
                if any(c["status"] == "failed" for c in checks)
                else "warning"
                if any(c["status"] == "warning" for c in checks)
                else "passed"
            )
            return {"status": status, "checks": checks}

    async def inspect_table(self, schema_name: str, table_name: str) -> dict:
        """Inspect one table immediately before a lifecycle operation."""
        async with self.connection() as conn:
            table = await conn.fetchrow(
                """
                SELECT c.oid, c.relreplident, c.reltuples::bigint AS estimated_rows,
                    pg_total_relation_size(c.oid) AS estimated_size_bytes,
                    pg_has_role(c.relowner, 'USAGE') AS owns_table,
                    has_table_privilege(c.oid, 'SELECT') AS can_select
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind = 'r'
                """,
                schema_name,
                table_name,
            )
            if table is None:
                return {
                    "ready": False,
                    "checks": [
                        {
                            "name": "table_exists",
                            "status": "fail",
                            "message": "Source table does not exist",
                        }
                    ],
                    "warnings": [],
                }
            keys = await conn.fetch(
                """
                SELECT a.attname AS name FROM pg_index i
                CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
                WHERE i.indrelid = $1 AND i.indisprimary ORDER BY k.ord
                """,
                table["oid"],
            )
            columns = await conn.fetch(
                """
                SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
                    NOT a.attnotnull AS nullable, a.attnum AS ordinal
                FROM pg_attribute a
                WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                table["oid"],
            )
            primary_keys = [row["name"] for row in keys]
            replica_identity = table["relreplident"]
            if isinstance(replica_identity, bytes):
                replica_identity = replica_identity.decode("ascii", errors="replace")
            warnings = []
            if not primary_keys:
                warnings.append(
                    {
                        "code": "NO_PRIMARY_KEY",
                        "message": "No primary key detected. Reliable key-based upserts, "
                        "deletes, and incremental snapshots may not be possible.",
                    }
                )
            if replica_identity == "d":
                warnings.append(
                    {
                        "code": "REPLICA_IDENTITY_DEFAULT",
                        "message": (
                            "Replica identity is DEFAULT; non-key old values are not available."
                        ),
                    }
                )
            checks = [
                {"name": "table_exists", "status": "pass"},
                {
                    "name": "primary_key",
                    "status": "pass" if primary_keys else "warning",
                },
                {
                    "name": "replica_identity",
                    "status": "pass" if replica_identity != "n" else "warning",
                    "value": replica_identity,
                },
                {
                    "name": "select_permission",
                    "status": "pass" if table["can_select"] else "fail",
                },
                {
                    "name": "publication_eligibility",
                    "status": "pass" if table["owns_table"] else "fail",
                },
                {"name": "supported_data_types", "status": "pass"},
            ]
            return {
                "ready": bool(table["can_select"] and table["owns_table"]),
                "checks": checks,
                "warnings": warnings,
                "primary_key_columns": primary_keys,
                "columns": [dict(column) for column in columns],
                "estimated_rows": table["estimated_rows"],
                "estimated_size_bytes": table["estimated_size_bytes"],
            }

    async def discover(self) -> list[dict]:
        async with self.connection() as conn:
            rows = await conn.fetch("""
                SELECT n.nspname AS schema_name, c.relname AS table_name, c.oid,
                    CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint END AS estimated_rows,
                    pg_total_relation_size(c.oid) AS estimated_size_bytes,
                    pg_has_role(c.relowner, 'USAGE') AS owns_table,
                    has_table_privilege(c.oid, 'SELECT') AS can_select
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                    AND n.nspname NOT LIKE 'pg_toast%'
                ORDER BY n.nspname, c.relname LIMIT 500
            """)
            tables = []
            for row in rows:
                columns = await conn.fetch(
                    """
                    SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
                        NOT a.attnotnull AS nullable, a.attnum AS ordinal
                    FROM pg_attribute a WHERE a.attrelid = $1 AND a.attnum > 0
                        AND NOT a.attisdropped ORDER BY a.attnum
                """,
                    row["oid"],
                )
                pk = await conn.fetch(
                    """
                    SELECT a.attname AS name FROM pg_index i
                    CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
                    WHERE i.indrelid = $1 AND i.indisprimary ORDER BY k.ord
                """,
                    row["oid"],
                )
                indexes = await conn.fetch(
                    """
                    SELECT indexname AS name, indexdef AS definition FROM pg_indexes
                    WHERE schemaname = $1 AND tablename = $2 ORDER BY indexname
                """,
                    row["schema_name"],
                    row["table_name"],
                )
                issues = []
                warnings = []
                if not pk:
                    warnings.append(
                        "No primary key; stable CDC keys and delete identity are unavailable"
                    )
                if not row["can_select"]:
                    issues.append("SELECT permission is required")
                if not row["owns_table"]:
                    issues.append(
                        "Table ownership is required to auto-create the filtered publication"
                    )
                tables.append(
                    {
                        "schema_name": row["schema_name"],
                        "table_name": row["table_name"],
                        "primary_key_columns": [r["name"] for r in pk],
                        "columns_json": [dict(r) for r in columns],
                        "indexes_json": [dict(r) for r in indexes],
                        "estimated_rows": row["estimated_rows"],
                        "estimated_size_bytes": row["estimated_size_bytes"],
                        "cdc_ready": not issues,
                        "cdc_status": "READY" if not issues and not warnings else "WARNING",
                        "cdc_issues": warnings + issues,
                    }
                )
            return tables
