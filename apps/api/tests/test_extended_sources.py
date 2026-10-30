from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.entities import Source
from app.providers import get_provider, provider_metadata
from app.providers.source_capabilities import validate_delivery
from app.schemas.connections import ConnectionInput
from app.schemas.requests import PipelineInput, TableSelection


def source(kind, **options):
    return Source(id=uuid4(), name="fixture", type=kind, host="db", port=3306,
                  username="cdc", database_name="inventory", ssl_enabled=True,
                  provider_options=options)


def pipeline(**overrides):
    return PipelineInput(name="fixture", source_id=uuid4(), kafka_cluster_id=uuid4(),
        connect_cluster_id=uuid4(), topic_prefix="fixture",
        tables=[TableSelection(schema_name="inventory", table_name="orders")], **overrides)


def test_mariadb_config_reuses_mysql_without_changing_it():
    mysql = get_provider("mysql").build_source_config(
        source("mysql", server_id=123), pipeline(), "secret", "name", "kafka:9092")
    maria = get_provider("mariadb").build_source_config(
        source("mariadb", server_id=123), pipeline(), "secret", "name", "kafka:9092")
    assert maria.pop("connector.class") == "io.debezium.connector.mariadb.MariaDbConnector"
    assert maria.pop("connect.timeout.ms") == "10000"
    assert maria.pop("database.ssl.mode") == "verify-full"
    assert maria.pop("event.converting.failure.handling.mode") == "fail"
    mysql.pop("database.ssl.mode")
    mysql.pop("connector.class")
    assert maria == mysql


def test_mariadb_source_only_secure_defaults():
    data = ConnectionInput(name="maria", provider="MARIADB", config={
        "host": "db", "database_name": "inventory", "username": "cdc"})
    assert data.config["port"] == 3306
    assert data.config["ssl_enabled"] is True
    assert data.capabilities == ["SOURCE"]
    with pytest.raises(ValidationError):
        ConnectionInput(name="maria", provider="MARIADB", config=data.config,
                        capabilities=["DESTINATION"])


def test_mariadb_version_and_unsafe_properties_are_not_mysql_fallbacks():
    from app.core.errors import DomainError

    with pytest.raises(DomainError, match=""):
        get_provider("mariadb").build_source_config(source("mariadb", server_id=123),
            pipeline(snapshot_mode="always"), "secret", "name", "kafka:9092")


@pytest.mark.asyncio
async def test_mariadb_index_discovery_excludes_prefix_indexes():
    from app.adapters.mariadb import MariaDBAdapter

    adapter = MariaDBAdapter(source("mariadb"), "secret")
    async def rows(connection, query, args):
        assert "expression" not in query.lower()
        return [{"name": "uq", "non_unique": 0, "column_name": "name", "sub_part": 10}]
    adapter._all = rows
    indexes = await adapter._indexes(None, "inventory", "orders")
    assert indexes[0]["partial"] is True


def test_capabilities_distinguish_verification_and_destination_guards():
    maria = next(p for p in provider_metadata() if p["type"] == "mariadb")
    assert maria["source_capabilities"]["status"] == "EXPERIMENTAL"
    assert maria["source_capabilities"]["verification"] == "NOT_RUN"
    validate_delivery("mariadb", "POSTGRESQL", "upsert")
    validate_delivery("postgresql", "CLICKHOUSE", "insert")


def test_sqlserver_config_namespace_tls_and_history():
    from app.providers.source_capabilities import source_topic

    db = source("sqlserver")
    data = pipeline()
    cfg = get_provider("sqlserver").build_source_config(db, data, "secret", "name", "kafka:9092")
    assert cfg["database.names"] == "inventory"
    assert "database.dbname" not in cfg
    assert cfg["database.encrypt"] == "true"
    assert cfg["database.trustServerCertificate"] == "false"
    assert cfg["table.include.list"] == r"inventory\.orders"
    assert cfg["schema.history.internal.kafka.bootstrap.servers"] == "kafka:9092"
    assert source_topic(db, "fixture", "sales", "orders") == "fixture.inventory.sales.orders"
    assert source_topic(source("postgresql"), "fixture", "sales", "orders") == "fixture.sales.orders"


def test_sqlserver_identifier_and_type_handling():
    from app.adapters.sqlserver import SQLServerAdapter
    from app.providers.types import parse_type

    assert SQLServerAdapter._quote("a]; DROP TABLE x--") == "[a]]; DROP TABLE x--]"
    for raw, kind in [("decimal(38,9)", "DECIMAL"), ("nvarchar(100)", "STRING"),
                      ("datetime2(7)", "DATETIME"), ("uniqueidentifier", "UUID"),
                      ("bit", "BOOLEAN"), ("varbinary(max)", "BYTES"), ("xml", "TEXT")]:
        assert parse_type("sqlserver", raw).kind == kind
    assert parse_type("sqlserver", "geography").kind == "UNSUPPORTED"


def test_new_sources_do_not_assume_non_primary_key_before_images():
    from app.models.entities import SourceTable
    from app.services.sync_strategy import assess

    table = SourceTable(schema_name="dbo", table_name="orders", primary_key_columns=[],
        columns_json=[{"name": "id", "type": "int", "nullable": False}],
        indexes_json=[{"name": "uq", "columns": ["id"], "unique": True, "valid": True}],
        source_properties_json={}, cdc_ready=True)
    assessment = assess(table, "sqlserver")
    assert assessment["capabilities"]["current_state"]["status"] == "UNSUPPORTED"
    assert assessment["capabilities"]["incremental_snapshot"] == "UNSUPPORTED"
    assert assessment["selected_sync_mode"] == "CHANGE_EVENT_LOG"
