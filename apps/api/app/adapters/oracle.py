"""Conservative Oracle LogMiner metadata; no administrative setup or SCN changes."""

from app.adapters.source_sql import SourceSQLAdapter, check, diagnostics


class OracleAdapter(SourceSQLAdapter):
    dialect = "oracle"
    TABLES_SQL = (
        "SELECT t.owner AS schema_name,t.table_name FROM all_tables t "
        "JOIN all_users u ON u.username=t.owner WHERE u.oracle_maintained='N' "
        "AND t.temporary='N' AND t.nested='NO' ORDER BY "
        "t.owner,t.table_name FETCH FIRST 500 ROWS ONLY"
    )

    async def list_databases(self):
        async with self.connection() as connection:
            return [
                r["name"]
                for r in await self._all(
                    connection, "SELECT name FROM v$containers ORDER BY con_id"
                )
            ]

    @staticmethod
    def _quote(value):
        # Catalog spelling is retained, including quoted mixed-case identifiers.
        return '"' + value.replace('"', '""') + '"'

    def _open(self, metadata=False):
        import oracledb

        options = self.source.provider_options
        service = (options.get("pdb_service_name") or options.get("pdb_name")) if metadata else None
        service = (
            service
            or options.get("service_name")
            or (None if options.get("sid") else self.source.database_name)
        )
        params = oracledb.ConnectParams(
            host=self.source.host,
            port=self.source.port,
            protocol="tcps" if self.source.ssl_enabled else "tcp",
            service_name=service if not options.get("sid") or metadata and service else None,
            sid=options.get("sid") if not service else None,
            ssl_server_dn_match=True,
            tcp_connect_timeout=int(options.get("connection_timeout_seconds", 10)),
        )
        connection = oracledb.connect(
            user=self.source.username, password=self.password, params=params
        )
        connection.call_timeout = int(options.get("connection_timeout_seconds", 10)) * 1000
        return connection

    def _connect(self):
        return self._open()

    def _connect_metadata(self):
        return self._open(metadata=True)

    async def test(self):
        async with self.connection() as connection:
            row = await self._one(
                connection,
                (
                    "SELECT version FROM product_component_version WHERE product "
                    "LIKE 'Oracle Database%' FETCH FIRST 1 ROWS ONLY"
                ),
            )
        return {
            "success": True,
            "status": "HEALTHY",
            "version": row["version"],
            "checks": [
                check("connection", True, True, True, "Check listener/service name"),
                check("authentication", True, True, True, "Check credentials"),
            ],
        }

    async def readiness(self):
        checks = []
        async with self.connection() as connection:
            for key, sql, predicate, expected, action, warning in (
                (
                    "server_version",
                    (
                        "SELECT version AS value FROM product_component_version WHERE "
                        "product LIKE 'Oracle Database%' FETCH FIRST 1 ROWS ONLY"
                    ),
                    lambda v: str(v).split(".")[0] in {"19", "21"},
                    "19c or 21c",
                    "Use an Oracle version compatible with Debezium 3.3 LogMiner",
                    False,
                ),
                (
                    "archivelog",
                    "SELECT log_mode AS value FROM v$database",
                    lambda v: v == "ARCHIVELOG",
                    "ARCHIVELOG",
                    "DBA must enable ARCHIVELOG with an approved database maintenance procedure",
                    False,
                ),
                (
                    "supplemental_logging",
                    "SELECT supplemental_log_data_min AS value FROM v$database",
                    lambda v: v in {"YES", "IMPLICIT"},
                    "YES",
                    "DBA: ALTER DATABASE ADD SUPPLEMENTAL LOG DATA;",
                    False,
                ),
                (
                    "redo_log_access",
                    "SELECT COUNT(*) AS value FROM v$log",
                    lambda v: bool(v),
                    "> 0",
                    "Grant access to required LogMiner V_$ views, including V_$LOG",
                    False,
                ),
                (
                    "archived_logs",
                    (
                        "SELECT COUNT(*) AS value FROM v$archived_log WHERE deleted='NO' "
                        "AND status='A' AND name IS NOT NULL"
                    ),
                    lambda v: bool(v),
                    "> 0 available archive logs",
                    "Make archive logs available and retain them longer than maximum outages",
                    False,
                ),
                (
                    "logmining_privilege",
                    "SELECT COUNT(*) AS value FROM session_privs WHERE privilege='LOGMINING'",
                    lambda v: bool(v),
                    "LOGMINING",
                    "DBA: GRANT LOGMINING TO the CDC account, with appropriate CDB scope",
                    False,
                ),
                (
                    "transaction_privilege",
                    (
                        "SELECT COUNT(*) AS value FROM session_privs WHERE "
                        "privilege='SELECT ANY TRANSACTION'"
                    ),
                    lambda v: bool(v),
                    "SELECT ANY TRANSACTION",
                    "DBA: grant SELECT ANY TRANSACTION to the CDC account",
                    False,
                ),
                (
                    "logminer_packages",
                    (
                        "SELECT COUNT(*) AS value FROM all_tab_privs WHERE owner='SYS' "
                        "AND table_name IN ('DBMS_LOGMNR','DBMS_LOGMNR_D') AND "
                        "privilege='EXECUTE' AND grantee IN (USER,'PUBLIC')"
                    ),
                    lambda v: v == 2,
                    "EXECUTE on both LogMiner packages",
                    "Grant EXECUTE on SYS.DBMS_LOGMNR and SYS.DBMS_LOGMNR_D directly",
                    False,
                ),
                (
                    "archive_retention",
                    (
                        "SELECT ROUND((SYSDATE-MIN(first_time))*86400) AS value FROM "
                        "v$archived_log WHERE deleted='NO' AND status='A'"
                    ),
                    lambda v: False,
                    "Longer than maximum outage",
                    (
                        "Verify physical archive availability, FRA space and retention "
                        "externally; catalog age alone is not a guarantee"
                    ),
                    True,
                ),
            ):
                try:
                    row = await self._one(connection, sql)
                    value = row.get("value") if row else None
                    if hasattr(value, "as_integer_ratio") and not isinstance(
                        value, (str, int, float)
                    ):
                        value = str(value)
                    checks.append(check(key, value, expected, predicate(value), action, warning))
                except Exception:
                    checks.append(check(key, None, expected, False, action, warning))
            try:
                topology = await self._one(
                    connection,
                    (
                        "SELECT cdb, SYS_CONTEXT('USERENV','CON_NAME') AS container_name "
                        "FROM v$database"
                    ),
                )
                valid = topology["cdb"] == "NO" or (
                    topology["container_name"] == "CDB$ROOT"
                    and bool(self.source.provider_options.get("pdb_name"))
                )
                checks.append(
                    check(
                        "cdb_pdb_topology",
                        topology,
                        "Non-CDB or CDB root plus explicit PDB",
                        valid,
                        (
                            "Use the CDB root mining service and set "
                            "pdb_name/pdb_service_name for discovery"
                        ),
                    )
                )
            except Exception:
                checks.append(
                    check(
                        "cdb_pdb_topology",
                        None,
                        "Visible topology",
                        False,
                        "Grant V_$DATABASE access and configure CDB/PDB services",
                    )
                )
        return {
            **diagnostics(checks),
            "monitoring_limited": True,
            "jdbc_driver": (
                "Validated separately by Kafka Connect config validation; plugin "
                "presence alone is insufficient"
            ),
        }

    async def inspect_table(self, schema_name, table_name):
        async with self.connection(metadata=True) as connection:
            columns = await self._all(
                connection,
                "SELECT column_name AS name,data_type AS "
                "type,data_precision,data_scale,data_length,"
                "char_length,nullable,column_id AS ordinal,default_length FROM all_tab_columns "
                "WHERE owner=:1 AND table_name=:2 ORDER BY column_id",
                (schema_name, table_name),
            )
            for col in columns:
                col["nullable"] = col["nullable"] == "Y"
                col["has_default"] = bool(col["default_length"])
                if col["type"] == "NUMBER" and col["data_precision"] is not None:
                    col["type"] = f"NUMBER({col['data_precision']},{col['data_scale'] or 0})"
                elif col["type"] in {"CHAR", "NCHAR", "VARCHAR2", "NVARCHAR2"}:
                    col["type"] = f"{col['type']}({col['char_length']})"
            indexes = self.indexes(
                await self._all(
                    connection,
                    "SELECT c.constraint_name AS name,CASE WHEN "
                    "c.constraint_type='P' THEN 1 ELSE 0 END AS is_primary,"
                    "1 AS is_unique,CASE WHEN c.status='ENABLED' AND "
                    "c.validated='VALIDATED' THEN 1 ELSE 0 END AS valid,"
                    "cc.column_name FROM all_constraints c JOIN all_cons_columns cc "
                    "ON c.owner=cc.owner "
                    "AND c.constraint_name=cc.constraint_name WHERE c.owner=:1 AND c.table_name=:2 "
                    "AND c.constraint_type IN ('P','U') ORDER BY c.constraint_name,cc.position",
                    (schema_name, table_name),
                )
            )
            primary: list[str] = next(
                (i["columns"] for i in indexes if i["primary"] and i["valid"]), []
            )
            logging = await self._one(
                connection,
                "SELECT COUNT(*) AS groups FROM all_log_groups WHERE owner=:1 AND table_name=:2 "
                "AND log_group_type='ALL COLUMN LOGGING' AND always='ALWAYS'",
                (schema_name, table_name),
            )
            database_logging = await self._one(
                connection, "SELECT supplemental_log_data_all AS enabled FROM v$database"
            )
            full = bool(logging and logging["groups"]) or bool(
                database_logging and database_logging["enabled"] == "YES"
            )
            from app.providers.types import parse_type

            unsupported = [
                c["name"] for c in columns if parse_type("oracle", c["type"]).kind == "UNSUPPORTED"
            ]
            issues = (
                [] if full else ["DBA must enable ALL COLUMNS supplemental logging on this table"]
            ) + (
                ["Unverified Oracle types in columns: " + ", ".join(unsupported)]
                if unsupported
                else []
            )
            return {
                "ready": bool(columns) and full and not unsupported,
                "columns": columns,
                "primary_key_columns": primary,
                "indexes": indexes,
                "source_properties": {
                    "supplemental_logging_all": full,
                    "non_pk_before_images_verified": False,
                    "collection_kind": "RELATIONAL",
                },
                "estimated_rows": None,
                "estimated_size_bytes": None,
                "issues": issues,
                "checks": [],
                "warnings": [],
            }
