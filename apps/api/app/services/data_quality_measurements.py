"""Read-only sessions over existing adapters. No user SQL is ever accepted."""

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import asyncpg

from app.adapters.mysql import MySQLAdapter
from app.adapters.postgres import PostgresAdapter
from app.core.errors import DomainError
from app.schemas.data_quality import Comparison, Dataset
from app.services.connections import credentials
from app.services.data_quality_types import canonical, normalize_timestamp

# Worker concurrency is bounded globally; each process allows one query per endpoint.
_limits: dict[tuple, asyncio.Semaphore] = {}


def quote(value: str, provider: str) -> str:
    if not value or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise DomainError("DQ_IDENTIFIER_INVALID", "Invalid dataset identifier", 422)
    delimiter = '"' if provider == "POSTGRESQL" else "`"
    return delimiter + value.replace(delimiter, delimiter * 2) + delimiter


class Reader:
    def __init__(self, connection, provider: str, timeout: int):
        self.connection = connection
        self.provider = provider
        self.timeout = timeout
        self.cache: dict = {}

    async def rows(self, sql: str, values: list | None = None) -> list[dict]:
        values = values or []
        try:
            async with asyncio.timeout(self.timeout):
                if self.provider == "POSTGRESQL":
                    return [dict(row) for row in await self.connection.fetch(sql, *values)]
                async with self.connection.cursor() as cursor:
                    await cursor.execute(sql, tuple(values))
                    # MySQL information_schema field names may be uppercase.
                    return [
                        {key.lower(): value for key, value in row.items()}
                        for row in await cursor.fetchall()
                    ]
        except Exception as exc:
            if isinstance(exc, asyncpg.InsufficientPrivilegeError) or (
                exc.args and exc.args[0] in (1142, 1143)
            ):
                raise DomainError(
                    "DQ_SELECT_FORBIDDEN", "Grant SELECT on the selected dataset", 422
                ) from exc
            if isinstance(exc, TimeoutError):
                raise DomainError(
                    "DQ_QUERY_TIMEOUT", "Read-only measurement exceeded query timeout", 503
                ) from exc
            raise

    def bind(self, values: list, value) -> str:
        values.append(value)
        return f"${len(values)}" if self.provider == "POSTGRESQL" else "%s"

    def table(self, dataset: Dataset) -> str:
        return f"{quote(dataset.schema_name, self.provider)}.{quote(dataset.table, self.provider)}"

    async def tables(self, database: str) -> list[dict]:
        values: list = []
        field = "table_catalog" if self.provider == "POSTGRESQL" else "table_schema"
        return await self.rows(
            "SELECT table_schema AS schema_name, table_name FROM information_schema.tables "
            f"WHERE {field}={self.bind(values, database)} AND table_type='BASE TABLE' "
            "AND table_schema NOT IN ('information_schema','pg_catalog','mysql',"
            "'performance_schema','sys') ORDER BY table_schema,table_name LIMIT 1000",
            values,
        )

    async def columns(self, dataset: Dataset) -> list[dict]:
        if dataset.schema_name in {
            "pg_catalog",
            "information_schema",
            "mysql",
            "sys",
            "performance_schema",
        }:
            raise DomainError(
                "DQ_DATASET_UNSUPPORTED", "System schemas are not eligible datasets", 422
            )
        values: list = []
        schema = self.bind(values, dataset.schema_name)
        table = self.bind(values, dataset.table)
        native = "udt_name" if self.provider == "POSTGRESQL" else "column_type"
        columns = await self.rows(
            "SELECT column_name, data_type, is_nullable, ordinal_position, "
            "numeric_precision, numeric_scale, character_maximum_length, datetime_precision, "
            f"character_set_name, {native} AS native_type FROM information_schema.columns "
            f"WHERE table_schema={schema} AND table_name={table} ORDER BY ordinal_position",
            values,
        )
        if not columns or len(columns) > 500:
            raise DomainError(
                "DQ_TABLE_UNAVAILABLE", "Table is missing or exceeds 500 columns", 422
            )
        # Exclude views and prove SELECT authorization independently on both endpoints.
        found = await self.rows(
            "SELECT table_type FROM information_schema.tables "
            f"WHERE table_schema={schema} AND table_name={table}",
            values,
        )
        if not found or found[0]["table_type"] != "BASE TABLE":
            raise DomainError("DQ_BASE_TABLE_REQUIRED", "Select a discoverable base table", 422)
        await self.rows(f"SELECT 1 FROM {self.table(dataset)} WHERE 1=0")
        primary = await self.rows(
            "SELECT k.column_name FROM information_schema.table_constraints t "
            "JOIN information_schema.key_column_usage k ON t.constraint_name=k.constraint_name "
            "AND t.table_schema=k.table_schema AND t.table_name=k.table_name "
            f"WHERE t.table_schema={schema} AND t.table_name={table} "
            "AND t.constraint_type='PRIMARY KEY' ORDER BY k.ordinal_position",
            values,
        )
        primary_names = [row["column_name"] for row in primary]
        for col in columns:
            col["canonical"] = canonical(col)
            col["primary_key_position"] = (
                primary_names.index(col["column_name"]) + 1
                if col["column_name"] in primary_names
                else None
            )
        return columns

    def condition(
        self, columns: list[dict], comparison: Comparison, side: str, window: dict
    ) -> tuple[str, list]:
        allow = {col["column_name"]: col for col in columns}
        clauses: list[str] = []
        values: list = []
        operations = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        for predicate in getattr(comparison, f"{side}_filters"):
            col = allow.get(predicate.column)
            if col is None:
                raise DomainError("DQ_FILTER_COLUMN", "Select an existing filter column", 422)
            name = quote(predicate.column, self.provider)
            if predicate.operator in {"is_null", "is_not_null"}:
                clauses.append(
                    f"{name} IS {'NOT ' if predicate.operator == 'is_not_null' else ''}NULL"
                )
                continue
            value = predicate.value
            family = col["canonical"]["family"]
            try:
                if family in {"integer", "decimal", "float"}:
                    value = Decimal(str(value))
                    if not value.is_finite() or (family == "integer" and value != int(value)):
                        raise ValueError()
                    value = int(value) if family == "integer" else value
                elif family == "boolean":
                    if not isinstance(value, bool):
                        raise ValueError()
                elif family == "timestamp":
                    value = datetime.fromisoformat(str(value))
                    zone = getattr(comparison, f"{side}_timezone")
                    value = self.timestamp_bound(value, col, zone)
                elif family == "date":
                    value = date.fromisoformat(str(value))
                elif family in {"string", "fixed_string"}:
                    if not isinstance(value, str) or len(value) > 1000:
                        raise ValueError()
                else:
                    raise ValueError()
            except (ValueError, TypeError, ArithmeticError) as exc:
                raise DomainError(
                    "DQ_FILTER_VALUE", "Filter value does not match column type", 422
                ) from exc
            clauses.append(f"{name} {operations[predicate.operator]} {self.bind(values, value)}")
        if comparison.scope == "TIME_WINDOW":
            name = getattr(comparison, f"{side}_time_column")
            col = allow.get(name)
            if not col or col["canonical"]["family"] != "timestamp":
                raise DomainError("DQ_WINDOW_COLUMN", "Time windows require timestamp columns", 422)
            zone = getattr(comparison, f"{side}_timezone")
            for key, op in (("start", ">="), ("end", "<")):
                bound = datetime.fromisoformat(window[key])
                bound = self.timestamp_bound(bound, col, zone)
                clauses.append(f"{quote(name, self.provider)} {op} {self.bind(values, bound)}")
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values

    def timestamp_bound(self, value: datetime, column: dict, zone: str | None):
        normalized = normalize_timestamp(value, zone)
        assert normalized is not None
        if column["canonical"]["aware"]:
            return normalized
        if not zone:
            raise ValueError("NAIVE_TIMEZONE_REQUIRED")
        return normalized.astimezone(ZoneInfo(zone)).replace(tzinfo=None)

    async def measure(
        self,
        dataset: Dataset,
        columns: list[dict],
        comparison: Comparison,
        side: str,
        window: dict,
        column: str | None = None,
    ) -> dict:
        where, values = self.condition(columns, comparison, side, window)
        if column and column not in {c["column_name"] for c in columns}:
            raise DomainError("DQ_TIMESTAMP_COLUMN", "Timestamp column is missing", 422)
        aggregate = f"MAX({quote(column, self.provider)})" if column else "COUNT(*)"
        sql = f"SELECT {aggregate} AS value FROM {self.table(dataset)}{where}"
        key = (sql, tuple(str(v) for v in values))
        if key not in self.cache:
            started = time.monotonic()
            rows = await self.rows(sql, values)
            self.cache[key] = {
                "value": rows[0]["value"],
                "observed_at": datetime.now(UTC).isoformat(),
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            }
        return self.cache[key]


@asynccontextmanager
async def reader(session, connection, timeout: int = 30):
    values = await credentials(session, connection)
    key = (connection.provider, connection.host, connection.port, connection.database_name)
    limiter = _limits.setdefault(key, asyncio.Semaphore(1))
    adapter = (
        PostgresAdapter(connection, values.get("password", ""))
        if connection.provider == "POSTGRESQL"
        else MySQLAdapter(connection, values.get("password", ""))
    )
    async with limiter, adapter.connection() as conn:
        if connection.provider == "POSTGRESQL":
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {int(timeout) * 1000}")
                await conn.execute("SET LOCAL TIME ZONE 'UTC'")
                yield Reader(conn, connection.provider, timeout)
        else:
            async with conn.cursor() as cursor:
                await cursor.execute("SET time_zone = '+00:00'")
                await cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout) * 1000}")
                await cursor.execute("START TRANSACTION READ ONLY")
            try:
                yield Reader(conn, connection.provider, timeout)
            finally:
                await conn.rollback()
