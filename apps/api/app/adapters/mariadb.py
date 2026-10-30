"""MariaDB wire protocol reuse with MariaDB-specific metadata and diagnostics."""

import re

from app.adapters.mysql import MySQLAdapter


class MariaDBAdapter(MySQLAdapter):
    async def _indexes(self, connection, schema_name: str, table_name: str) -> list[dict]:
        # MariaDB statistics has no MySQL 8 EXPRESSION column. Prefix indexes are
        # not valid full-value identities and must not become effective keys.
        rows = await self._all(
            connection,
            "SELECT index_name AS name, non_unique AS non_unique, column_name AS column_name, "
            "sub_part AS sub_part FROM information_schema.statistics "
            "WHERE table_schema=%s AND table_name=%s ORDER BY index_name, seq_in_index",
            (schema_name, table_name),
        )
        grouped: dict[str, dict] = {}
        for row in rows:
            index = grouped.setdefault(row["name"], {
                "name": row["name"], "unique": not bool(row["non_unique"]),
                "primary": row["name"] == "PRIMARY", "valid": True,
                "partial": False, "expression": False, "columns": [], "definition": "",
            })
            index["partial"] |= row.get("sub_part") is not None
            if row["column_name"]:
                index["columns"].append(row["column_name"])
            else:
                index["expression"] = True
        for index in grouped.values():
            index["definition"] = ",".join(index["columns"])
        return list(grouped.values())

    async def readiness(self) -> dict:
        async with self.connection(select_database=False) as connection:
            version = await self._one(connection, "SELECT VERSION() AS version")
            raw = str((version or {}).get("version", ""))
            match = re.search(r"(\d+)\.(\d+)\.(\d+)(?=[^0-9]*-MariaDB)", raw, re.I)
            supported = bool(match and tuple(map(int, match.groups()[:2])) in {
                (10, 6), (10, 11), (11, 4),
            })
            variables = {
                row["Variable_name"].lower(): str(row["Value"])
                for row in await self._all(connection,
                    "SHOW GLOBAL VARIABLES WHERE Variable_name IN "
                    "('log_bin','binlog_format','binlog_row_image','server_id',"
                    "'expire_logs_days','binlog_expire_logs_seconds','gtid_strict_mode',"
                    "'gtid_domain_id','log_slave_updates','binlog_legacy_event_pos')")
            }
            checks = [self._check("connection", True, True, True, "Check network"),
                      self._check("authentication", True, True, True, "Check credentials"),
                      self._check("server_version", raw, "10.6/10.11/11.4 MariaDB", supported,
                                  "Use a MariaDB release compatible with Debezium 3.3.2")]
            for key, expected in (("log_bin", "ON"), ("binlog_format", "ROW"),
                                  ("binlog_row_image", "FULL")):
                checks.append(self._check(key, variables.get(key), expected,
                    variables.get(key, "").upper() == expected, f"Set {key}={expected}"))
            identity = int(variables.get("server_id", "0"))
            checks.append(self._check("server_id", identity, "> 0", identity > 0,
                                      "Configure a unique non-zero MariaDB server_id"))
            grants = " ".join(str(next(iter(row.values()))) for row in await self._all(
                connection, "SHOW GRANTS FOR CURRENT_USER" )).upper()
            for privilege in ("SELECT", "RELOAD", "SHOW DATABASES", "REPLICATION SLAVE",
                              "BINLOG MONITOR", "LOCK TABLES"):
                passed = "ALL PRIVILEGES" in grants or privilege in grants
                if privilege == "BINLOG MONITOR":
                    passed |= "REPLICATION CLIENT" in grants
                checks.append(self._check("permission_" + privilege.lower().replace(" ", "_"),
                    passed, True, passed, f"Grant {privilege} to the CDC account"))
            retention = int(float(variables.get("binlog_expire_logs_seconds", "0") or 0))
            retention = retention or int(float(variables.get("expire_logs_days", "0")) * 86400)
            outage = self.source.provider_options.get("max_connector_outage_seconds")
            risk = bool(retention and isinstance(outage, int) and outage >= retention)
            checks.append(self._check("binlog_retention", retention or None, "Longer than outage",
                bool(retention and outage and retention > outage),
                "Set binlog retention and max_connector_outage_seconds; monitor oldest binlog",
                "warning"))
            # GTID settings are reported, not rewritten or treated as interchangeable
            # with MySQL UUID GTIDs. Replicated topologies need separate validation.
            checks.append(self._check("gtid_topology", variables.get("gtid_strict_mode"),
                "Standalone; replicated topology requires validation", False,
                "Verify GTID domain IDs and log_slave_updates before using a replica", "warning"))
            status = "failed" if any(c["status"] == "failed" for c in checks) else "warning"
            return {"success": status != "failed", "status": status, "checks": checks,
                    "version": raw, "server_id": identity, "gtid": variables,
                    "binlog_retention_seconds": retention or None,
                    "retention_risk": risk, "monitoring_limited": True}

    async def validate_custom_key(self, schema_name, table_name, key_columns):
        from app.adapters.relational_keys import validate_custom_key

        return await validate_custom_key(self, schema_name, table_name, key_columns, "mariadb")
