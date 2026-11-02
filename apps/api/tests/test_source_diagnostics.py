from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.adapters.connect import classify_task_failure
from app.adapters.mariadb import MariaDBAdapter
from app.adapters.oracle import OracleAdapter
from app.adapters.sqlserver import SQLServerAdapter
from app.models.entities import Source


def database(kind):
    return Source(
        name="fixture",
        type=kind,
        host="db",
        port=1234,
        database_name="inventory",
        username="cdc",
        ssl_enabled=True,
        provider_options={},
    )


@asynccontextmanager
async def connection(**kwargs):
    yield None


async def test_mariadb_readiness_rejects_mysql_and_missing_full_binlogs():
    adapter = MariaDBAdapter(database("mariadb"), "secret")
    adapter.connection = connection
    adapter._one = AsyncMock(return_value={"version": "8.4.0 MySQL"})
    adapter._all = AsyncMock(
        side_effect=[
            [
                {"Variable_name": "server_id", "Value": "0"},
                {"Variable_name": "binlog_format", "Value": "STATEMENT"},
            ],
            [{"grants": "GRANT USAGE"}],
        ]
    )
    result = await adapter.readiness()
    failed = {c["key"] for c in result["checks"] if c["status"] == "failed"}
    assert {"server_version", "binlog_format", "binlog_row_image", "server_id"} <= failed
    assert result["success"] is False


@pytest.mark.parametrize(
    "legacy,compression,ready", [("OFF", "OFF", False), ("ON", "ON", False), ("ON", "OFF", True)]
)
async def test_mariadb_requires_durable_resume_settings(legacy, compression, ready):
    adapter = MariaDBAdapter(database("mariadb"), "secret")
    adapter.connection = connection
    adapter._one = AsyncMock(return_value={"version": "11.4.13-MariaDB-log"})
    variables = {
        "server_id": "123",
        "log_bin": "ON",
        "binlog_format": "ROW",
        "binlog_row_image": "FULL",
        "binlog_legacy_event_pos": legacy,
        "log_bin_compress": compression,
    }
    adapter._all = AsyncMock(
        side_effect=[
            [{"Variable_name": key, "Value": value} for key, value in variables.items()],
            [{"grants": "GRANT ALL PRIVILEGES ON *.*"}],
        ]
    )
    result = await adapter.readiness()
    assert result["success"] is ready


async def test_sqlserver_readiness_keeps_missing_visibility_unknown():
    adapter = SQLServerAdapter(database("sqlserver"), "secret")
    adapter.connection = connection
    adapter._one = AsyncMock(
        side_effect=[
            {
                "version": "16.0.4000",
                "edition": "Developer Edition",
                "engine_edition": 3,
                "is_cdc_enabled": 0,
                "database_name": "inventory",
            },
            {"ok": 0},
            {"ok": 0},
            PermissionError(),
            PermissionError(),
            PermissionError(),
        ]
    )
    result = await adapter.readiness()
    assert result["success"] is False
    checks = {c["key"]: c for c in result["checks"]}
    assert checks["database_cdc"]["status"] == "failed"
    assert checks["sql_server_agent"]["current_value"] is None
    assert checks["cleanup_retention"]["current_value"] is None
    assert result["monitoring_limited"] is True


async def test_sqlserver_discovery_blocks_incomplete_capture_and_preserves_schema():
    adapter = SQLServerAdapter(database("sqlserver"), "secret")
    adapter.connection = connection
    adapter._one = AsyncMock(return_value={"object_id": 123, "is_tracked_by_cdc": True})
    adapter._all = AsyncMock(
        side_effect=[
            [
                {
                    "name": "id",
                    "type": "int",
                    "max_length": 4,
                    "precision": 10,
                    "scale": 0,
                    "nullable": False,
                    "ordinal": 1,
                },
                {
                    "name": "label",
                    "type": "nvarchar",
                    "max_length": 100,
                    "precision": 0,
                    "scale": 0,
                    "nullable": True,
                    "ordinal": 2,
                },
            ],
            [{"name": "pk", "is_primary": True, "is_unique": True, "column_name": "id"}],
            [{"capture_instance": "sales_orders", "column_name": "id"}],
        ]
    )
    metadata = await adapter.inspect_table("sales", "orders")
    assert metadata["ready"] is False
    assert metadata["columns"][1]["type"] == "nvarchar(50)"
    assert metadata["primary_key_columns"] == ["id"]
    assert metadata["source_properties"]["capture_instances"] == ["sales_orders"]
    assert adapter._one.call_args.args[2] == ("sales", "orders")


@pytest.mark.parametrize("can_select", [True, False])
async def test_sqlserver_selected_table_requires_select_permission(can_select):
    adapter = SQLServerAdapter(database("sqlserver"), "secret")
    adapter.connection = connection
    adapter._one = AsyncMock(
        return_value={"object_id": 123, "is_tracked_by_cdc": True, "can_select": can_select}
    )
    adapter._all = AsyncMock(
        side_effect=[
            [
                {
                    "name": "id",
                    "type": "int",
                    "max_length": 4,
                    "precision": 10,
                    "scale": 0,
                    "nullable": False,
                    "ordinal": 1,
                }
            ],
            [{"name": "pk", "is_primary": True, "is_unique": True, "column_name": "id"}],
            [{"capture_instance": "sales_orders", "column_name": "id"}],
        ]
    )
    assert (await adapter.inspect_table("sales", "orders"))["ready"] is can_select


async def test_mariadb_select_probe_quotes_identifiers_and_never_reads_rows(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.mysql.MySQLAdapter.inspect_table", AsyncMock(return_value={"ready": True})
    )
    adapter = MariaDBAdapter(database("mariadb"), "secret")
    adapter.connection = connection
    adapter._all = AsyncMock(return_value=[])
    await adapter.inspect_table("inventory", "name`with`quotes")
    assert (
        adapter._all.call_args.args[1] == "SELECT 1 FROM `inventory`.`name``with``quotes` LIMIT 0"
    )


async def test_oracle_readiness_reports_missing_logminer_privileges_without_writing():
    adapter = OracleAdapter(database("oracle"), "secret")
    adapter.connection = connection

    async def read(_, sql, args=()):
        assert sql.startswith("SELECT")
        if "cdb," in sql:
            return {"cdb": "YES", "container_name": "ORCLPDB1"}
        if "product_component" in sql:
            return {"value": "19.0.0.0.0"}
        if "log_mode" in sql:
            return {"value": "NOARCHIVELOG"}
        if "supplemental_log_data_min" in sql:
            return {"value": "NO"}
        raise PermissionError()

    adapter._one = read
    result = await adapter.readiness()
    assert result["success"] is False
    failed = {c["key"] for c in result["checks"] if c["status"] == "failed"}
    assert {
        "archivelog",
        "supplemental_logging",
        "logmining_privilege",
        "cdb_pdb_topology",
    } <= failed
    assert result["jdbc_driver"].startswith("Validated separately")


@pytest.mark.parametrize(
    "trace",
    [
        "ChangeStreamHistoryLost",
        "resume token was not found",
        "ORA-01291: missing logfile",
        "ORA-00308 cannot open archived log",
        "LSN is no longer available",
    ],
)
def test_expired_history_is_recovery_required_and_never_retryable(trace):
    failure = classify_task_failure(trace, "source")
    assert failure.code == "SOURCE_RECOVERY_REQUIRED"
    assert failure.category == "RECOVERY_REQUIRED"
    assert failure.retryable is False
    assert "explicitly approved" in failure.message


def test_source_migration_preserves_old_records_and_refuses_destructive_downgrade():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    path = Path(__file__).parents[1] / "migrations/versions/0014_extended_cdc_sources.py"
    spec = importlib.util.spec_from_file_location("source_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE connections (id VARCHAR PRIMARY KEY, provider VARCHAR, "
                "secret_ref VARCHAR, CONSTRAINT ck_connections_provider "
                "CHECK (provider IN ('POSTGRESQL','MYSQL')))"
            )
        )
        conn.execute(text("INSERT INTO connections VALUES ('old','MYSQL','unchanged-secret-ref')"))
        before = conn.execute(text("SELECT * FROM connections")).all()
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert conn.execute(text("SELECT * FROM connections")).all() == before
            for provider in ("MARIADB", "SQLSERVER", "MONGODB", "ORACLE"):
                conn.execute(
                    text("INSERT INTO connections VALUES (:id,:provider,'new-secret')"),
                    {"id": provider, "provider": provider},
                )
            with pytest.raises(RuntimeError, match="explicitly"):
                migration.downgrade()
            assert conn.execute(text("SELECT COUNT(*) FROM connections")).scalar() == 5
    engine.dispose()
