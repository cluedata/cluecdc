"""Small DB-API bridge for new relational sources, preserving driver thread affinity."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from app.core.errors import DomainError


def check(key, current, expected, passed, action, warning=False):
    return {
        "key": key,
        "name": key.replace("_", " ").title(),
        "status": "passed" if passed else "warning" if warning else "failed",
        "severity": "warning" if warning else "error",
        "current_value": current,
        "expected_value": expected,
        "recommended_action": None if passed else action,
    }


def diagnostics(checks):
    status = (
        "failed"
        if any(c["status"] == "failed" for c in checks)
        else ("warning" if any(c["status"] == "warning" for c in checks) else "passed")
    )
    return {"success": status != "failed", "status": status, "checks": checks}


def database_error(error):
    text = str(error).lower()
    if any(value in text for value in ("18456", "ora-01017", "login failed")):
        return DomainError("SOURCE_AUTH_FAILED", "Source rejected the supplied credentials", 422)
    if any(value in text for value in ("certificate", "ssl", "tls", "ora-29024")):
        return DomainError("SSL_ERROR", "Source TLS certificate verification failed", 422)
    if any(value in text for value in ("permission", "ora-01031", "ora-00942", "229")):
        return DomainError(
            "SOURCE_PERMISSION_DENIED", "Required source metadata permissions are missing", 422
        )
    return DomainError(
        "SOURCE_UNAVAILABLE", "Source connection or read-only metadata query failed", 503
    )


class SourceSQLAdapter:
    dialect = ""
    TABLES_SQL = ""

    def _connect(self):
        raise NotImplementedError

    async def inspect_table(self, schema_name, table_name):
        raise NotImplementedError

    def __init__(self, source, password):
        self.source, self.password = source, password

    @asynccontextmanager
    async def connection(self, metadata=False):
        # Every operation for a connection runs on the same thread, including close.
        executor = ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_running_loop()
        database = None
        try:
            connect = (
                self._connect_metadata
                if metadata and hasattr(self, "_connect_metadata")
                else self._connect
            )
            database = await loop.run_in_executor(executor, connect)

            async def query(sql, args):
                def execute():
                    cursor = database.cursor()
                    try:
                        cursor.execute(sql, args) if args else cursor.execute(sql)
                        names = [str(column[0]).lower() for column in cursor.description or []]
                        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
                    finally:
                        cursor.close()

                return await loop.run_in_executor(executor, execute)

            yield query
        except DomainError:
            raise
        except ImportError:
            raise DomainError(
                "SOURCE_DRIVER_MISSING",
                "Install the source database driver on the API/worker image",
                422,
            ) from None
        except Exception as error:
            raise database_error(error) from None
        finally:
            if database is not None:
                await loop.run_in_executor(executor, database.close)
            executor.shutdown(wait=False)

    async def _all(self, connection, sql, args=()):
        return await connection(sql, args)

    async def _one(self, connection, sql, args=()):
        rows = await self._all(connection, sql, args)
        return rows[0] if rows else None

    async def validate_custom_key(self, schema_name, table_name, key_columns):
        from app.adapters.relational_keys import validate_custom_key

        return await validate_custom_key(self, schema_name, table_name, key_columns, self.dialect)

    async def discover(self):
        async with self.connection(metadata=True) as connection:
            rows = await self._all(connection, self.TABLES_SQL)
        tables = []
        for row in rows:
            metadata = await self.inspect_table(row["schema_name"], row["table_name"])
            tables.append(
                {
                    "schema_name": row["schema_name"],
                    "table_name": row["table_name"],
                    "primary_key_columns": metadata["primary_key_columns"],
                    "columns_json": metadata["columns"],
                    "indexes_json": metadata["indexes"],
                    "source_properties_json": metadata["source_properties"],
                    "estimated_rows": metadata.get("estimated_rows"),
                    "estimated_size_bytes": None,
                    "cdc_ready": metadata["ready"],
                    "cdc_status": "READY" if metadata["ready"] else "BLOCKED",
                    "cdc_issues": metadata.get("issues", []),
                }
            )
        return tables

    @staticmethod
    def indexes(rows):
        grouped: dict[str, dict] = {}
        for row in rows:
            index = grouped.setdefault(
                row["name"],
                {
                    "name": row["name"],
                    "primary": bool(row["is_primary"]),
                    "unique": bool(row["is_unique"]),
                    "valid": bool(row.get("valid", True)),
                    "partial": bool(row.get("partial", False)),
                    "expression": False,
                    "columns": [],
                    "definition": "",
                },
            )
            index["columns"].append(row["column_name"])
        for index in grouped.values():
            index["definition"] = ",".join(index["columns"])
        return list(grouped.values())
