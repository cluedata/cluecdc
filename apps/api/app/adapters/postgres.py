import asyncio
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
    async def validate_custom_key(
        self, schema_name: str, table_name: str, key_columns: list[str]
    ) -> dict: ...


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
            retention_risk = False
            slot_details: list[dict] = []
            monitoring_limited = False
            try:
                slots = await conn.fetch(
                    "SELECT slot_name, active, restart_lsn::text AS restart_lsn, wal_status, "
                    "CASE WHEN restart_lsn IS NULL THEN NULL ELSE "
                    "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint END "
                    "AS retained_wal_bytes FROM pg_replication_slots "
                    "WHERE slot_type='logical' AND database=current_database() "
                    "ORDER BY slot_name"
                )
                slot_details = [
                    {
                        "slot_name": row["slot_name"],
                        "active": bool(row["active"]),
                        "restart_lsn": row["restart_lsn"],
                        "wal_status": row["wal_status"],
                        "retained_wal_bytes": row["retained_wal_bytes"],
                    }
                    for row in slots
                ]
                lost = [slot for slot in slot_details if slot["wal_status"] == "lost"]
                inactive = [slot for slot in slot_details if not slot["active"]]
                retention_risk = bool(lost or inactive)
                check(
                    "replication_slot_health",
                    {
                        "logical_slots": len(slot_details),
                        "inactive_slots": len(inactive),
                        "lost_slots": len(lost),
                    },
                    "No lost or unexpectedly inactive slots",
                    not lost,
                    "A lost slot cannot resume; assess the consistency boundary and plan an "
                    "explicit resnapshot",
                )
                check(
                    "inactive_replication_slots",
                    len(inactive),
                    0,
                    not inactive,
                    "Identify the owning connector and resume it, or have a DBA review the "
                    "slot before any manual cleanup",
                    "warning",
                )
                retained = [
                    slot["retained_wal_bytes"]
                    for slot in slot_details
                    if slot["retained_wal_bytes"] is not None
                ]
                check(
                    "retained_wal_bytes",
                    max(retained, default=0),
                    "Measured bytes per logical slot; no time capacity inferred",
                    not inactive,
                    "Monitor source disk capacity and investigate inactive slots; retained "
                    "bytes cannot be converted to a safe outage duration without workload data",
                    "warning",
                )
            except asyncpg.PostgresError:
                monitoring_limited = True
                check(
                    "replication_slot_monitoring",
                    "permission_limited",
                    "Visible logical slot state",
                    False,
                    "Grant a monitoring role access to replication slot metadata or monitor "
                    "slots externally",
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
            return {
                "status": status,
                "checks": checks,
                "replication_slots": slot_details,
                "retention_risk": retention_risk,
                "monitoring_limited": monitoring_limited,
            }

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
                    NOT a.attnotnull AS nullable, a.attnum AS ordinal,
                    ad.adbin IS NOT NULL AS has_default
                FROM pg_attribute a
                LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
                WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                table["oid"],
            )
            indexes = await conn.fetch(
                """
                SELECT ci.relname AS name, i.indisunique AS "unique",
                    i.indisprimary AS "primary", i.indisvalid AS valid,
                    i.indpred IS NOT NULL AS partial,
                    i.indexprs IS NOT NULL AS expression,
                    i.indisreplident AS replica_identity,
                    pg_get_indexdef(i.indexrelid) AS definition,
                    array_remove(array_agg(a.attname ORDER BY k.ord), NULL) AS columns,
                    bool_and(a.attnotnull) FILTER (WHERE k.attnum > 0) AS all_not_null
                FROM pg_index i
                JOIN pg_class ci ON ci.oid = i.indexrelid
                LEFT JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
                LEFT JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
                WHERE i.indrelid = $1
                GROUP BY ci.relname, i.indisunique, i.indisprimary, i.indisvalid,
                    i.indpred, i.indexprs, i.indisreplident, i.indexrelid
                ORDER BY ci.relname
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
                "indexes": [
                    {
                        **dict(index),
                        "columns": list(index["columns"] or []),
                        "replica_identity_eligible": bool(
                            index["unique"]
                            and index["valid"]
                            and not index["partial"]
                            and not index["expression"]
                            and index["all_not_null"]
                        ),
                    }
                    for index in indexes
                ],
                "source_properties": {"replica_identity": replica_identity},
                "estimated_rows": table["estimated_rows"],
                "estimated_size_bytes": table["estimated_size_bytes"],
            }

    async def validate_custom_key(
        self, schema_name: str, table_name: str, key_columns: list[str]
    ) -> dict:
        metadata = await self.inspect_table(schema_name, table_name)
        columns = {column["name"]: column for column in metadata.get("columns", [])}
        missing = [column for column in key_columns if column not in columns]
        if missing:
            return {
                "status": "INVALID",
                "enforced": False,
                "verified": False,
                "errors": ["One or more selected columns no longer exist"],
            }
        if any(columns[column]["nullable"] for column in key_columns):
            return {
                "status": "INVALID",
                "enforced": False,
                "verified": False,
                "errors": ["Custom key columns must be declared NOT NULL"],
            }
        candidate = next(
            (
                index
                for index in metadata.get("indexes", [])
                if index.get("unique")
                and index.get("valid")
                and not index.get("partial")
                and not index.get("expression")
                and list(index.get("columns") or []) == key_columns
            ),
            None,
        )

        def quote(value: str) -> str:
            return '"' + value.replace('"', '""') + '"'

        qualified = f"{quote(schema_name)}.{quote(table_name)}"
        fields = ",".join(quote(column) for column in key_columns)
        try:
            async with asyncio.timeout(10):
                async with self.connection() as conn:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL statement_timeout = '5000ms'")
                        has_null = await conn.fetchval(
                            f"SELECT EXISTS (SELECT 1 FROM {qualified} WHERE "
                            + " OR ".join(f"{quote(column)} IS NULL" for column in key_columns)
                            + " LIMIT 1)"
                        )
                        duplicate = await conn.fetchval(
                            f"SELECT EXISTS (SELECT 1 FROM {qualified} GROUP BY {fields} "
                            "HAVING COUNT(*) > 1 LIMIT 1)"
                        )
        except TimeoutError as exc:
            raise DomainError(
                "CUSTOM_KEY_VALIDATION_TIMEOUT",
                "Custom key validation exceeded the bounded query timeout",
                422,
            ) from exc
        if has_null or duplicate:
            errors = []
            if has_null:
                errors.append("At least one selected key column contains NULL")
            if duplicate:
                errors.append("Duplicate key combinations exist")
            return {
                "status": "INVALID",
                "enforced": False,
                "verified": False,
                "errors": errors,
            }
        index_name = f"cluecdc_{table_name}_effective_key"
        return {
            "status": "DATABASE_ENFORCED" if candidate else "VERIFIED_ONLY",
            "enforced": bool(candidate),
            "verified": True,
            "constraint_name": candidate.get("name") if candidate else None,
            "errors": [],
            "warning": None
            if candidate
            else "A one-time scan does not guarantee future uniqueness",
            "suggested_sql": None
            if candidate
            else f"CREATE UNIQUE INDEX CONCURRENTLY {quote(index_name)} ON {qualified} ({fields});",
        }

    async def discover(self) -> list[dict]:
        async with self.connection() as conn:
            rows = await conn.fetch("""
                SELECT n.nspname AS schema_name, c.relname AS table_name, c.oid,
                    c.relreplident,
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
                        NOT a.attnotnull AS nullable, a.attnum AS ordinal,
                        ad.adbin IS NOT NULL AS has_default
                    FROM pg_attribute a
                    LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
                    WHERE a.attrelid = $1 AND a.attnum > 0
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
                    SELECT ci.relname AS name, i.indisunique AS "unique",
                        i.indisprimary AS "primary", i.indisvalid AS valid,
                        i.indpred IS NOT NULL AS partial,
                        i.indexprs IS NOT NULL AS expression,
                        i.indisreplident AS replica_identity,
                        pg_get_indexdef(i.indexrelid) AS definition,
                        array_remove(array_agg(a.attname ORDER BY k.ord), NULL) AS columns,
                        bool_and(a.attnotnull) FILTER (WHERE k.attnum > 0) AS all_not_null
                    FROM pg_index i
                    JOIN pg_class ci ON ci.oid = i.indexrelid
                    LEFT JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
                    LEFT JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
                    WHERE i.indrelid = $1
                    GROUP BY ci.relname, i.indisunique, i.indisprimary, i.indisvalid,
                        i.indpred, i.indexprs, i.indisreplident, i.indexrelid
                    ORDER BY ci.relname
                """,
                    row["oid"],
                )
                issues = []
                warnings = []
                if not pk:
                    eligible_unique = next(
                        (
                            index
                            for index in indexes
                            if index["unique"]
                            and not index["primary"]
                            and index["valid"]
                            and not index["partial"]
                            and not index["expression"]
                            and index["all_not_null"]
                        ),
                        None,
                    )
                    if eligible_unique and row["relreplident"] in {"f", b"f"}:
                        warnings.append(
                            "No primary key; using an enforced unique key for current-state "
                            "sync. Incremental snapshots remain unavailable"
                        )
                    elif eligible_unique:
                        warnings.append(
                            "A unique key is available, but REPLICA IDENTITY FULL is required "
                            "for safe UPDATE and DELETE synchronization"
                        )
                    else:
                        warnings.append(
                            "No stable row identity; use append-only capture or a supported "
                            "change-event log strategy"
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
                        "indexes_json": [
                            {
                                **dict(index),
                                "columns": list(index["columns"] or []),
                                "replica_identity_eligible": bool(
                                    index["unique"]
                                    and index["valid"]
                                    and not index["partial"]
                                    and not index["expression"]
                                    and index["all_not_null"]
                                ),
                            }
                            for index in indexes
                        ],
                        "source_properties_json": {
                            "replica_identity": (
                                row["relreplident"].decode("ascii", errors="replace")
                                if isinstance(row["relreplident"], bytes)
                                else row["relreplident"]
                            )
                        },
                        "estimated_rows": row["estimated_rows"],
                        "estimated_size_bytes": row["estimated_size_bytes"],
                        "cdc_ready": not issues,
                        "cdc_status": "READY" if not issues and not warnings else "WARNING",
                        "cdc_issues": warnings + issues,
                    }
                )
            return tables
