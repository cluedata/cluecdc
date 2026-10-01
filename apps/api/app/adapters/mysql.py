from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiomysql
from aiomysql.cursors import DictCursor

from app.core.config import get_settings
from app.core.errors import DomainError
from app.models.entities import Destination, Source


def _error(error: Exception, role: str = "SOURCE") -> DomainError:
    code = error.args[0] if error.args and isinstance(error.args[0], int) else None
    prefix = role.upper()
    if code == 1045:
        return DomainError(
            f"{prefix}_AUTH_FAILED", f"{role.title()} rejected the supplied credentials", 422
        )
    if code == 1049:
        return DomainError("DATABASE_NOT_FOUND", "Selected MySQL database does not exist", 422)
    if code == 2026:
        return DomainError(
            "SSL_ERROR", "MySQL TLS negotiation or certificate validation failed", 422
        )
    return DomainError(
        f"{prefix}_UNAVAILABLE", f"{role.title()} database connection or query failed", 503
    )


class MySQLAdapter:
    def __init__(self, source: Source | Destination, password: str, role: str = "source"):
        self.source = source
        self.password = password
        self.role = role

    @asynccontextmanager
    async def connection(self, select_database: bool = True) -> AsyncIterator[aiomysql.Connection]:
        connection = None
        try:
            timeout = int(
                self.source.provider_options.get(
                    "connection_timeout_seconds", get_settings().integration_timeout_seconds
                )
            )
            ssl = {"check_hostname": True} if self.source.ssl_enabled else None
            connection = await aiomysql.connect(
                host=self.source.host,
                port=self.source.port,
                db=self.source.database_name if select_database else None,
                user=self.source.username,
                password=self.password,
                connect_timeout=timeout,
                autocommit=True,
                ssl=ssl,
                cursorclass=DictCursor,
                charset="utf8mb4",
            )
            yield connection
        except DomainError:
            raise
        except Exception as exc:
            raise _error(exc, self.role) from exc
        finally:
            if connection:
                connection.close()

    async def _one(self, connection, query: str, args: tuple = ()) -> dict | None:
        async with connection.cursor() as cursor:
            await cursor.execute(query, args)
            return await cursor.fetchone()

    async def _all(self, connection, query: str, args: tuple = ()) -> list[dict]:
        async with connection.cursor() as cursor:
            await cursor.execute(query, args)
            return list(await cursor.fetchall())

    @staticmethod
    def _check(
        key: str,
        current,
        expected,
        passed: bool,
        action: str,
        severity: str = "error",
    ) -> dict:
        return {
            "key": key,
            "name": key.replace("_", " ").title(),
            "status": "passed" if passed else "warning" if severity == "warning" else "failed",
            "severity": severity,
            "description": f"Verify MySQL {key.replace('_', ' ')}",
            "current_value": current,
            "expected_value": expected,
            "recommended_action": None if passed else action,
        }

    async def test(self) -> dict:
        async with self.connection(select_database=False) as connection:
            version = await self._one(connection, "SELECT VERSION() AS version")
            return {
                "status": "HEALTHY",
                "version": version["version"] if version else None,
                "success": True,
                "checks": [
                    self._check("connection", True, True, True, "Check network settings"),
                    self._check("authentication", True, True, True, "Verify credentials"),
                ],
            }

    async def readiness(self) -> dict:
        async with self.connection(select_database=False) as connection:
            checks = [
                self._check("connection", True, True, True, "Check network settings"),
                self._check("authentication", True, True, True, "Verify credentials"),
            ]
            version = await self._one(connection, "SELECT VERSION() AS value")
            major = int(str(version["value"]).split(".")[0]) if version else 0
            checks.append(
                self._check(
                    "server_version",
                    version["value"] if version else None,
                    ">= 8.0",
                    major >= 8,
                    "Upgrade to a supported MySQL 8 release",
                )
            )
            variables = {
                row["Variable_name"].lower(): str(row["Value"])
                for row in await self._all(
                    connection,
                    "SHOW GLOBAL VARIABLES WHERE Variable_name IN "
                    "('log_bin','binlog_format','binlog_row_image','server_id')",
                )
            }
            checks.extend(
                [
                    self._check(
                        "binlog_enabled",
                        variables.get("log_bin"),
                        "ON",
                        variables.get("log_bin", "").upper() == "ON",
                        "Enable log_bin and restart MySQL",
                    ),
                    self._check(
                        "binlog_format",
                        variables.get("binlog_format"),
                        "ROW",
                        variables.get("binlog_format", "").upper() == "ROW",
                        "Set binlog_format=ROW",
                    ),
                    self._check(
                        "binlog_row_image",
                        variables.get("binlog_row_image"),
                        "FULL",
                        variables.get("binlog_row_image", "").upper() == "FULL",
                        "Set binlog_row_image=FULL",
                    ),
                ]
            )
            grants = " ".join(
                row[next(iter(row))]
                for row in await self._all(connection, "SHOW GRANTS FOR CURRENT_USER")
            )
            upper = grants.upper()
            replication = "ALL PRIVILEGES" in upper or (
                "REPLICATION SLAVE" in upper and "REPLICATION CLIENT" in upper
            )
            checks.append(
                self._check(
                    "replication_permissions",
                    replication,
                    True,
                    replication,
                    "Grant REPLICATION SLAVE and REPLICATION CLIENT",
                )
            )
            database = await self._one(
                connection,
                "SELECT COUNT(*) AS present FROM information_schema.schemata WHERE schema_name=%s",
                (self.source.database_name,),
            )
            checks.append(
                self._check(
                    "database_exists",
                    bool(database and database["present"]),
                    True,
                    bool(database and database["present"]),
                    "Create the database or select an existing one",
                )
            )
            cipher = await self._one(connection, "SHOW STATUS LIKE 'Ssl_cipher'")
            tls = bool(cipher and cipher.get("Value"))
            checks.append(
                self._check(
                    "ssl",
                    tls,
                    self.source.ssl_enabled,
                    tls or not self.source.ssl_enabled,
                    "Configure a trusted MySQL TLS certificate",
                    "warning",
                )
            )
            status = (
                "failed"
                if any(c["status"] == "failed" for c in checks)
                else "warning"
                if any(c["status"] == "warning" for c in checks)
                else "passed"
            )
            return {
                "success": status != "failed",
                "status": status,
                "checks": checks,
                "server_id": variables.get("server_id"),
            }

    async def inspect_table(self, schema_name: str, table_name: str) -> dict:
        async with self.connection() as connection:
            table = await self._one(
                connection,
                "SELECT table_rows AS estimated_rows, "
                "data_length + index_length AS estimated_size_bytes "
                "FROM information_schema.tables WHERE table_schema=%s "
                "AND table_name=%s AND table_type='BASE TABLE'",
                (schema_name, table_name),
            )
            if not table:
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
            columns = await self._all(
                connection,
                "SELECT column_name AS name, column_type AS type, "
                "is_nullable='YES' AS nullable, ordinal_position AS ordinal "
                "FROM information_schema.columns WHERE table_schema=%s "
                "AND table_name=%s ORDER BY ordinal_position",
                (schema_name, table_name),
            )
            keys = await self._all(
                connection,
                "SELECT column_name AS name FROM information_schema.key_column_usage "
                "WHERE table_schema=%s AND table_name=%s "
                "AND constraint_name='PRIMARY' ORDER BY ordinal_position",
                (schema_name, table_name),
            )
            primary = [row["name"] for row in keys]
            warnings = (
                []
                if primary
                else [
                    {
                        "code": "NO_PRIMARY_KEY",
                        "message": "No primary key detected. Reliable upserts, deletes, "
                        "and incremental snapshots may not be possible.",
                    }
                ]
            )
            return {
                "ready": True,
                "checks": [
                    {"name": "table_exists", "status": "pass"},
                    {"name": "primary_key", "status": "pass" if primary else "warning"},
                    {"name": "select_permission", "status": "pass"},
                    {"name": "supported_data_types", "status": "pass"},
                ],
                "warnings": warnings,
                "primary_key_columns": primary,
                "columns": columns,
                "estimated_rows": table["estimated_rows"],
                "estimated_size_bytes": table["estimated_size_bytes"],
            }

    async def discover(self) -> list[dict]:
        async with self.connection() as connection:
            rows = await self._all(
                connection,
                "SELECT table_schema AS schema_name, table_name AS table_name, "
                "table_rows AS estimated_rows, "
                "data_length + index_length AS estimated_size_bytes FROM information_schema.tables "
                "WHERE table_type='BASE TABLE' AND table_schema NOT IN "
                "('information_schema','mysql','performance_schema','sys') "
                "ORDER BY table_schema, table_name LIMIT 500",
            )
            tables = []
            for row in rows:
                schema, table = row["schema_name"], row["table_name"]
                columns = await self._all(
                    connection,
                    "SELECT column_name AS name, column_type AS type, "
                    "is_nullable='YES' AS nullable, ordinal_position AS ordinal "
                    "FROM information_schema.columns WHERE table_schema=%s "
                    "AND table_name=%s ORDER BY ordinal_position",
                    (schema, table),
                )
                keys = await self._all(
                    connection,
                    "SELECT column_name AS name FROM information_schema.key_column_usage "
                    "WHERE table_schema=%s AND table_name=%s "
                    "AND constraint_name='PRIMARY' ORDER BY ordinal_position",
                    (schema, table),
                )
                indexes = await self._all(
                    connection,
                    "SELECT index_name AS name, "
                    "GROUP_CONCAT(column_name ORDER BY seq_in_index) AS definition "
                    "FROM information_schema.statistics WHERE table_schema=%s "
                    "AND table_name=%s GROUP BY index_name ORDER BY index_name",
                    (schema, table),
                )
                issues = (
                    []
                    if keys
                    else ["No primary key; stable CDC keys and delete identity are unavailable"]
                )
                tables.append(
                    {
                        "schema_name": schema,
                        "table_name": table,
                        "primary_key_columns": [key["name"] for key in keys],
                        "columns_json": columns,
                        "indexes_json": indexes,
                        "estimated_rows": row["estimated_rows"],
                        "estimated_size_bytes": row["estimated_size_bytes"],
                        "cdc_ready": True,
                        "cdc_status": "READY" if keys else "WARNING",
                        "cdc_issues": issues,
                    }
                )
            return tables
