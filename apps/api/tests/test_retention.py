from contextlib import asynccontextmanager

import aiomysql
import asyncpg
import pytest
from sqlalchemy import select

from app.adapters.mysql import MySQLAdapter
from app.adapters.postgres import PostgresAdapter
from app.alerts.monitoring import observe_source_retention
from app.models.entities import Alert, Source
from app.schemas.requests import SourceInput


def source(provider: str = "postgresql", **options) -> Source:
    return Source(
        type=provider,
        host="source",
        port=5432 if provider == "postgresql" else 3306,
        database_name="commerce",
        username="cdc",
        ssl_enabled=False,
        provider_options=options,
    )


class PostgresReadinessConnection:
    def __init__(self, *, permission_limited: bool = False):
        self.permission_limited = permission_limited

    async def fetchval(self, query):
        values = {
            "SHOW server_version_num": "170000",
            "SHOW wal_level": "logical",
            "SHOW max_replication_slots": "10",
            "SHOW max_wal_senders": "10",
        }
        if query in values:
            return values[query]
        if "rolreplication" in query or "has_database_privilege" in query:
            return True
        if "count(*) FROM pg_replication_slots" in query:
            return 2
        raise AssertionError(query)

    async def fetch(self, query):
        if "FROM pg_replication_slots" not in query:
            raise AssertionError(query)
        if self.permission_limited:
            raise asyncpg.InsufficientPrivilegeError("permission denied")
        return [
            {
                "slot_name": "cluecdc_active",
                "active": True,
                "restart_lsn": "0/100",
                "wal_status": "reserved",
                "retained_wal_bytes": 1024,
            },
            {
                "slot_name": "cluecdc_inactive",
                "active": False,
                "restart_lsn": "0/80",
                "wal_status": "extended",
                "retained_wal_bytes": 4096,
            },
        ]


async def postgres_readiness(monkeypatch, *, permission_limited: bool = False):
    adapter = PostgresAdapter(source(), "private")

    @asynccontextmanager
    async def connection():
        yield PostgresReadinessConnection(permission_limited=permission_limited)

    monkeypatch.setattr(adapter, "connection", connection)
    return await adapter.readiness()


async def test_postgres_readiness_surfaces_inactive_slot_and_retained_wal(monkeypatch):
    result = await postgres_readiness(monkeypatch)

    assert result["status"] == "warning"
    assert result["retention_risk"] is True
    assert result["replication_slots"][1]["retained_wal_bytes"] == 4096
    checks = {check["key"]: check for check in result["checks"]}
    assert checks["inactive_replication_slots"]["status"] == "warning"
    assert checks["retained_wal_bytes"]["current_value"] == 4096


async def test_postgres_slot_monitoring_permission_is_a_warning(monkeypatch):
    result = await postgres_readiness(monkeypatch, permission_limited=True)

    assert result["monitoring_limited"] is True
    assert result["retention_risk"] is False
    check = next(item for item in result["checks"] if item["key"] == "replication_slot_monitoring")
    assert check["status"] == "warning"


async def test_mysql_readiness_compares_binlog_retention_to_outage(monkeypatch):
    adapter = MySQLAdapter(source("mysql", max_connector_outage_seconds=7200), "private")

    @asynccontextmanager
    async def connection(select_database=False):
        yield object()

    async def one(connection, query, args=()):
        if "VERSION()" in query:
            return {"value": "8.4.0"}
        if "information_schema.schemata" in query:
            return {"present": 1}
        if "Ssl_cipher" in query:
            return {"Value": ""}
        if "BINARY LOG STATUS" in query:
            return {"File": "mysql-bin.000002", "Position": 321}
        raise AssertionError(query)

    async def all_rows(connection, query, args=()):
        if "SHOW GLOBAL VARIABLES" in query:
            return [
                {"Variable_name": "log_bin", "Value": "ON"},
                {"Variable_name": "binlog_format", "Value": "ROW"},
                {"Variable_name": "binlog_row_image", "Value": "FULL"},
                {"Variable_name": "server_id", "Value": "123"},
                {"Variable_name": "binlog_expire_logs_seconds", "Value": "3600"},
            ]
        if "SHOW GRANTS" in query:
            return [{"Grants": "GRANT ALL PRIVILEGES ON *.* TO `cdc`@`%`"}]
        if "SHOW BINARY LOGS" in query:
            return [
                {"Log_name": "mysql-bin.000001", "File_size": 1000},
                {"Log_name": "mysql-bin.000002", "File_size": 2000},
            ]
        raise AssertionError(query)

    monkeypatch.setattr(adapter, "connection", connection)
    monkeypatch.setattr(adapter, "_one", one)
    monkeypatch.setattr(adapter, "_all", all_rows)

    result = await adapter.readiness()

    assert result["retention_risk"] is True
    assert result["binlog_retention_seconds"] == 3600
    assert result["binlog_inventory"]["total_bytes"] == 3000
    check = next(item for item in result["checks"] if item["key"] == "binlog_retention_window")
    assert check["status"] == "warning"


async def test_mysql_position_permission_is_a_warning(monkeypatch):
    adapter = MySQLAdapter(source("mysql"), "private")

    @asynccontextmanager
    async def connection(select_database=False):
        yield object()

    async def one(connection, query, args=()):
        if "VERSION()" in query:
            return {"value": "8.4.0"}
        if "information_schema.schemata" in query:
            return {"present": 1}
        if "Ssl_cipher" in query:
            return {"Value": ""}
        if "BINARY LOG STATUS" in query or "MASTER STATUS" in query:
            raise aiomysql.OperationalError(1227, "access denied")
        raise AssertionError(query)

    async def all_rows(connection, query, args=()):
        if "SHOW GLOBAL VARIABLES" in query:
            return [
                {"Variable_name": "log_bin", "Value": "ON"},
                {"Variable_name": "binlog_format", "Value": "ROW"},
                {"Variable_name": "binlog_row_image", "Value": "FULL"},
                {"Variable_name": "server_id", "Value": "123"},
            ]
        if "SHOW GRANTS" in query:
            return [{"Grants": "GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO cdc"}]
        raise AssertionError(query)

    monkeypatch.setattr(adapter, "connection", connection)
    monkeypatch.setattr(adapter, "_one", one)
    monkeypatch.setattr(adapter, "_all", all_rows)

    result = await adapter.readiness()

    assert result["status"] == "warning"
    assert result["monitoring_limited"] is True
    assert result["binlog_position"] is None


async def test_retention_alert_fires_and_resolves(db_factory):
    async with db_factory() as db:
        await observe_source_retention(
            db,
            identifier="source-1",
            name="Commerce",
            database_type="postgresql",
            firing=True,
            details={"replication_slots": [{"slot_name": "inactive", "active": False}]},
        )
        await db.commit()
        alert = await db.scalar(select(Alert))
        assert alert is not None
        assert alert.event_type == "CDC_SOURCE_LOG_RETENTION_RISK"
        assert alert.severity == "critical"
        assert alert.status == "firing"

        await observe_source_retention(
            db,
            identifier="source-1",
            name="Commerce",
            database_type="postgresql",
            firing=False,
            details={"replication_slots": []},
        )
        await db.commit()
        await db.refresh(alert)
        assert alert.status == "resolved"


def test_maximum_connector_outage_validation():
    data = {
        "name": "source",
        "type": "mysql",
        "host": "mysql",
        "port": 3306,
        "database_name": "commerce",
        "username": "cdc",
        "provider_options": {"max_connector_outage_seconds": 604800},
    }
    assert SourceInput(**data).provider_options["max_connector_outage_seconds"] == 604800
    with pytest.raises(ValueError, match="Maximum connector outage"):
        SourceInput(**{**data, "provider_options": {"max_connector_outage_seconds": 299}})
