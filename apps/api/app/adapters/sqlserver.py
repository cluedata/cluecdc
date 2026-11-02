from app.adapters.source_sql import SourceSQLAdapter, check, diagnostics


class SQLServerAdapter(SourceSQLAdapter):
    dialect = "sqlserver"
    TABLES_SQL = (
        "SELECT TOP (500) s.name AS schema_name,t.name AS table_name "
        "FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id "
        "WHERE t.is_ms_shipped=0 AND s.name NOT IN ('cdc','sys') ORDER BY s.name,t.name"
    )

    async def list_databases(self):
        async with self.connection() as connection:
            return [
                r["name"]
                for r in await self._all(
                    connection,
                    "SELECT name FROM sys.databases WHERE database_id>4 "
                    "AND state_desc='ONLINE' AND HAS_DBACCESS(name)=1 ORDER BY name",
                )
            ]

    @staticmethod
    def _quote(value):
        return "[" + value.replace("]", "]]") + "]"

    def _connect(self):
        import mssql_python

        from app.services.azure_synapse.connection import _odbc

        # Credential values cannot inject extra ODBC properties.
        text = (
            f"Server={_odbc('tcp:' + self.source.host + ',' + str(self.source.port))};"
            f"Database={_odbc(self.source.database_name)};UID={_odbc(self.source.username)};"
            f"PWD={_odbc(self.password)};Encrypt={'yes' if self.source.ssl_enabled else 'no'};"
            "TrustServerCertificate=no;"
        )
        timeout = int(self.source.provider_options.get("connection_timeout_seconds", 10))
        connection = mssql_python.connect(text, timeout=timeout)
        connection.timeout = timeout
        return connection

    async def test(self):
        async with self.connection() as connection:
            row = await self._one(
                connection,
                "SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(64)) AS version",
            )
        return {
            "status": "HEALTHY",
            "success": True,
            "version": row["version"],
            "checks": [
                check("connection", True, True, True, "Check network"),
                check("authentication", True, True, True, "Check credentials"),
            ],
        }

    async def readiness(self):
        async with self.connection() as connection:
            info = await self._one(
                connection,
                "SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(64)) AS version, "
                "CAST(SERVERPROPERTY('Edition') AS varchar(128)) AS edition, "
                "CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition, "
                "is_cdc_enabled,DB_NAME() AS database_name FROM sys.databases WHERE name=DB_NAME()",
            )
            major = int(info["version"].split(".")[0])
            if info["database_name"] != self.source.database_name:
                from app.core.errors import DomainError

                raise DomainError(
                    "SOURCE_DATABASE_IDENTITY_MISMATCH",
                    "Use the exact catalog spelling of the SQL Server database name "
                    "for stable topic identity",
                    422,
                )
            edition = str(info["edition"])
            checks = [
                check("connection", True, True, True, "Check network"),
                check("authentication", True, True, True, "Check credentials"),
                check(
                    "server_version_edition",
                    f"{info['version']} {edition}",
                    "SQL Server 2017/2019/2022 Standard/Enterprise/Developer",
                    major in {14, 15, 16}
                    and any(
                        word in edition.lower() for word in ("standard", "enterprise", "developer")
                    ),
                    "Use a supported SQL Server release and CDC edition",
                ),
                check(
                    "database_cdc",
                    bool(info["is_cdc_enabled"]),
                    True,
                    bool(info["is_cdc_enabled"]),
                    "DBA: USE [database]; EXEC sys.sp_cdc_enable_db;",
                ),
            ]
            for key, sql, action, warning in (
                (
                    "select_permission",
                    "SELECT COUNT(*) AS ok FROM sys.tables t JOIN sys.schemas s "
                    "ON s.schema_id=t.schema_id WHERE t.is_tracked_by_cdc=1 AND "
                    "HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(t.name),'OBJECT','SELECT')=1",
                    "Grant SELECT on captured tables and CDC capture tables",
                    False,
                ),
                (
                    "view_database_state",
                    "SELECT HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','VIEW DATABASE STATE') AS ok",
                    (
                        "Grant VIEW DATABASE STATE (and VIEW DATABASE PERFORMANCE STATE "
                        "on SQL Server 2022)"
                    ),
                    False,
                ),
                (
                    "sql_server_agent",
                    (
                        "SELECT COUNT(*) AS ok FROM sys.dm_server_services WHERE "
                        "servicename LIKE 'SQL Server Agent%' AND status_desc='Running'"
                    ),
                    "Start SQL Server Agent; grant VIEW SERVER STATE to inspect services",
                    False,
                ),
                (
                    "capture_instances",
                    "SELECT COUNT(*) AS ok FROM cdc.change_tables",
                    (
                        "DBA: EXEC sys.sp_cdc_enable_table @source_schema=N'dbo', "
                        "@source_name=N'table', @role_name=NULL;"
                    ),
                    False,
                ),
                (
                    "cleanup_retention",
                    (
                        "SELECT MAX(retention) AS ok FROM msdb.dbo.cdc_jobs WHERE "
                        "database_id=DB_ID() AND job_type='cleanup'"
                    ),
                    "DBA: inspect sys.sp_cdc_help_jobs and retain CDC changes longer than outages",
                    True,
                ),
            ):
                try:
                    value = await self._one(connection, sql)
                    current = value.get("ok") if value else None
                    checks.append(check(key, current, "> 0", bool(current), action, warning))
                except Exception:
                    # Missing visibility is never presented as success or zero.
                    checks.append(
                        check(key, None, "Accessible and configured", False, action, warning)
                    )
            return {
                **diagnostics(checks),
                "version": info["version"],
                "edition": edition,
                "monitoring_limited": any(c["current_value"] is None for c in checks),
            }

    async def inspect_table(self, schema_name, table_name):
        async with self.connection() as connection:
            table = await self._one(
                connection,
                "SELECT t.object_id,t.is_tracked_by_cdc,"
                "HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(t.name),"
                "'OBJECT','SELECT') AS can_select "
                "FROM sys.tables t JOIN sys.schemas s "
                "ON s.schema_id=t.schema_id WHERE s.name=? AND t.name=?",
                (schema_name, table_name),
            )
            if not table:
                return {
                    "ready": False,
                    "issues": ["Table missing or not visible"],
                    "primary_key_columns": [],
                    "columns": [],
                    "indexes": [],
                    "source_properties": {},
                }
            object_id = table["object_id"]
            columns = await self._all(
                connection,
                "SELECT c.name,ty.name AS type,c.max_length,c.precision,c.scale,"
                "c.is_nullable AS nullable,c.column_id AS ordinal,"
                "CASE WHEN c.default_object_id>0 THEN 1 ELSE 0 END AS has_default "
                "FROM sys.columns c JOIN sys.types ty ON c.user_type_id=ty.user_type_id "
                "WHERE c.object_id=? ORDER BY c.column_id",
                (object_id,),
            )
            for col in columns:
                base = col["type"]
                if base in {"decimal", "numeric"}:
                    col["type"] = f"{base}({col['precision']},{col['scale']})"
                elif base in {"varchar", "nvarchar", "char", "nchar", "varbinary", "binary"}:
                    size = col["max_length"]
                    size = size // 2 if base.startswith("n") and size > 0 else size
                    col["type"] = f"{base}({'max' if size < 0 else size})"
                elif base in {"datetime2", "time", "datetimeoffset"}:
                    col["type"] = f"{base}({col['scale']})"
            indexes = self.indexes(
                await self._all(
                    connection,
                    "SELECT i.name,i.is_primary_key AS is_primary,i.is_unique,"
                    "CASE WHEN i.is_disabled=0 THEN 1 ELSE 0 END AS "
                    "valid,i.has_filter AS partial,c.name AS column_name "
                    "FROM sys.indexes i JOIN sys.index_columns ic ON "
                    "i.object_id=ic.object_id AND i.index_id=ic.index_id "
                    "JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id "
                    "WHERE i.object_id=? AND ic.key_ordinal>0 ORDER BY i.index_id,ic.key_ordinal",
                    (object_id,),
                )
            )
            primary: list[str] = next(
                (i["columns"] for i in indexes if i["primary"] and i["valid"]), []
            )
            capture = (
                await self._all(
                    connection,
                    "SELECT ct.capture_instance,cc.column_name FROM cdc.change_tables ct "
                    "JOIN cdc.captured_columns cc ON ct.object_id=cc.object_id WHERE "
                    "ct.source_object_id=?",
                    (object_id,),
                )
                if table["is_tracked_by_cdc"]
                else []
            )
            captured = {c["column_name"] for c in capture}
            complete = bool(capture) and all(c["name"] in captured for c in columns)
            issues = (
                []
                if complete
                else [
                    (
                        "Enable table CDC and capture all selected columns; rediscover "
                        "after schema changes"
                    )
                ]
            )
            from app.providers.types import parse_type

            unsupported = [
                c["name"]
                for c in columns
                if parse_type("sqlserver", c["type"]).kind == "UNSUPPORTED"
            ]
            if unsupported:
                issues.append("Unverified SQL Server types in columns: " + ", ".join(unsupported))
                complete = False
            if not table.get("can_select"):
                issues.append("Grant SELECT on all captured source columns to the CDC account")
                complete = False
            return {
                "ready": complete,
                "columns": columns,
                "indexes": indexes,
                "primary_key_columns": primary,
                "estimated_rows": None,
                "estimated_size_bytes": None,
                "source_properties": {
                    "cdc_enabled": bool(table["is_tracked_by_cdc"]),
                    "capture_instances": sorted({r["capture_instance"] for r in capture}),
                    "non_pk_before_images_verified": False,
                },
                "issues": issues,
                "checks": [],
                "warnings": [],
            }
