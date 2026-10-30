from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.entities import Source
from app.providers import get_provider, provider_metadata
from app.providers.source_capabilities import validate_delivery
from app.schemas.connections import ConnectionInput
from app.schemas.requests import PipelineInput, TableSelection


def source(kind, **options):
    return Source(
        id=uuid4(),
        name="fixture",
        type=kind,
        host="db",
        port=3306,
        username="cdc",
        database_name="inventory",
        ssl_enabled=True,
        provider_options=options,
    )


def pipeline(**overrides):
    return PipelineInput(
        name="fixture",
        source_id=uuid4(),
        kafka_cluster_id=uuid4(),
        connect_cluster_id=uuid4(),
        topic_prefix="fixture",
        tables=[TableSelection(schema_name="inventory", table_name="orders")],
        **overrides,
    )


def test_mariadb_config_reuses_mysql_without_changing_it():
    mysql = get_provider("mysql").build_source_config(
        source("mysql", server_id=123), pipeline(), "secret", "name", "kafka:9092"
    )
    maria = get_provider("mariadb").build_source_config(
        source("mariadb", server_id=123), pipeline(), "secret", "name", "kafka:9092"
    )
    assert maria.pop("connector.class") == "io.debezium.connector.mariadb.MariaDbConnector"
    assert maria.pop("connect.timeout.ms") == "10000"
    assert maria.pop("database.ssl.mode") == "verify-full"
    assert maria.pop("event.converting.failure.handling.mode") == "fail"
    mysql.pop("database.ssl.mode")
    mysql.pop("connector.class")
    assert maria == mysql


def test_mariadb_source_only_secure_defaults():
    data = ConnectionInput(
        name="maria",
        provider="MARIADB",
        config={"host": "db", "database_name": "inventory", "username": "cdc"},
    )
    assert data.config["port"] == 3306
    assert data.config["ssl_enabled"] is True
    assert data.capabilities == ["SOURCE"]
    with pytest.raises(ValidationError):
        ConnectionInput(
            name="maria", provider="MARIADB", config=data.config, capabilities=["DESTINATION"]
        )


def test_mariadb_version_and_unsafe_properties_are_not_mysql_fallbacks():
    from app.core.errors import DomainError

    with pytest.raises(DomainError, match=""):
        get_provider("mariadb").build_source_config(
            source("mariadb", server_id=123),
            pipeline(snapshot_mode="always"),
            "secret",
            "name",
            "kafka:9092",
        )


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
    assert maria["source_capabilities"]["verification"] == "LIVE_FIXTURE_PARTIAL"
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
    assert (
        source_topic(source("postgresql"), "fixture", "sales", "orders") == "fixture.sales.orders"
    )


def test_sqlserver_identifier_and_type_handling():
    from app.adapters.sqlserver import SQLServerAdapter
    from app.providers.types import parse_type

    assert SQLServerAdapter._quote("a]; DROP TABLE x--") == "[a]]; DROP TABLE x--]"
    for raw, kind in [
        ("decimal(38,9)", "DECIMAL"),
        ("nvarchar(100)", "STRING"),
        ("datetime2(7)", "DATETIME"),
        ("uniqueidentifier", "UUID"),
        ("bit", "BOOLEAN"),
        ("varbinary(max)", "BYTES"),
        ("xml", "TEXT"),
    ]:
        assert parse_type("sqlserver", raw).kind == kind
    assert parse_type("sqlserver", "geography").kind == "UNSUPPORTED"


def test_new_sources_do_not_assume_non_primary_key_before_images():
    from app.models.entities import SourceTable
    from app.services.sync_strategy import assess

    table = SourceTable(
        schema_name="dbo",
        table_name="orders",
        primary_key_columns=[],
        columns_json=[{"name": "id", "type": "int", "nullable": False}],
        indexes_json=[{"name": "uq", "columns": ["id"], "unique": True, "valid": True}],
        source_properties_json={},
        cdc_ready=True,
    )
    assessment = assess(table, "sqlserver")
    assert assessment["capabilities"]["current_state"]["status"] == "UNSUPPORTED"
    assert assessment["capabilities"]["incremental_snapshot"] == "UNSUPPORTED"
    assert assessment["selected_sync_mode"] == "CHANGE_EVENT_LOG"


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://u:p@host/db?tlsInsecure=true",
        "mongodb://u:p@host/db?tls=false",
        "http://u:p@host",
        "mongodb://u:p@host/?tlsAllowInvalidHostnames=true",
    ],
)
def test_mongodb_rejects_insecure_uri(uri):
    with pytest.raises(ValidationError):
        ConnectionInput(
            name="mongo",
            provider="MONGODB",
            config={"database_name": "inventory"},
            credentials={"connection_uri": uri},
        )


async def test_mongodb_uri_crud_is_encrypted_redacted_and_retained(client, db_factory):
    from sqlalchemy import select

    from app.models.entities import Connection, SecretReference
    from app.services.secrets import secret_provider

    uri = "mongodb+srv://alice:uri-private-password@cluster.example.net/?authSource=admin"
    payload = {
        "name": "mongo",
        "provider": "MONGODB",
        "config": {"database_name": "inventory"},
        "credentials": {"connection_uri": uri},
    }
    response = await client.post("/api/v1/connections", json=payload)
    assert response.status_code == 201
    assert uri not in response.text and "uri-private-password" not in response.text
    identifier = response.json()["id"]
    payload["credentials"] = {}
    payload["name"] = "mongo-renamed"
    edited = await client.put(f"/api/v1/connections/{identifier}", json=payload)
    assert edited.status_code == 200
    view = await client.get(f"/api/v1/sources/{identifier}")
    assert view.status_code == 200 and uri not in view.text
    async with db_factory() as session:
        connection = await session.scalar(
            select(Connection).where(Connection.name == "mongo-renamed")
        )
        secret = await session.get(SecretReference, connection.secret_ref)
        assert uri not in secret.ciphertext
        assert (await secret_provider(session).get_secret(secret.id))["connection_uri"] == uri
    deleted = await client.delete(f"/api/v1/connections/{identifier}")
    assert deleted.status_code == 200


async def test_mongodb_validation_never_returns_uri_or_password(client):
    uri = "mongodb://alice:private-mongo-password@host/?tlsInsecure=true"
    response = await client.post(
        "/api/v1/connections",
        json={
            "name": "mongo",
            "provider": "MONGODB",
            "config": {"database_name": "inventory"},
            "credentials": {"connection_uri": uri},
        },
    )
    assert response.status_code == 422
    assert uri not in response.text and "private-mongo-password" not in response.text


def test_mongodb_envelopes_are_never_relational_upserts():
    from app.core.errors import DomainError
    from app.models.entities import SourceTable
    from app.services.sync_strategy import assess

    db = source("mongodb")
    cfg = get_provider("mongodb").build_source_config(
        db, pipeline(), "${cluecdc:reference:connection_uri}", "name", "kafka:9092"
    )
    assert cfg["capture.mode"] == "change_streams"
    assert "database.password" not in cfg
    assert "table.include.list" not in cfg
    assert "transforms" not in cfg
    table = SourceTable(
        schema_name="inventory",
        table_name="orders",
        primary_key_columns=["_id"],
        columns_json=[{"name": "_id", "type": "BSON", "nullable": False}],
        indexes_json=[],
        source_properties_json={"collection_kind": "DOCUMENT"},
        cdc_ready=True,
    )
    assert assess(table, "mongodb")["selected_sync_mode"] == "CHANGE_EVENT_LOG"
    for target in ("POSTGRESQL", "MYSQL", "CLICKHOUSE", "BIGQUERY", "AZURE_SYNAPSE", "REDSHIFT"):
        with pytest.raises(DomainError):
            validate_delivery("mongodb", target, "upsert")
    validate_delivery("mongodb", "MINIO", "insert")


def test_oracle_logminer_configuration_and_quoted_identity():
    from app.adapters.oracle import OracleAdapter
    from app.providers.types import parse_type

    db = source("oracle", service_name="ORCLCDB", pdb_name="ORCLPDB1")
    cfg = get_provider("oracle").build_source_config(db, pipeline(), "secret", "name", "kafka:9092")
    assert cfg["database.connection.adapter"] == "logminer"
    assert cfg["database.pdb.name"] == "ORCLPDB1"
    assert (
        "PROTOCOL=TCPS" in cfg["database.url"] and "SSL_SERVER_DN_MATCH=YES" in cfg["database.url"]
    )
    assert "SERVICE_NAME=ORCLCDB" in cfg["database.url"]
    assert cfg["decimal.handling.mode"] == "precise"
    assert "scn" not in cfg and "snapshot.mode" in cfg
    assert OracleAdapter._quote('Order"Items') == '"Order""Items"'
    assert parse_type("oracle", "NUMBER(38,9)").precision == 38
    assert parse_type("oracle", "DATE").kind == "DATETIME"
    for raw in (
        "NUMBER",
        "NUMBER(10,-2)",
        "XMLTYPE",
        "BLOB",
        "CLOB",
        "TIMESTAMP(6) WITH LOCAL TIME ZONE",
    ):
        assert parse_type("oracle", raw).kind == "UNSUPPORTED"


@pytest.mark.parametrize("kind", ["MARIADB", "SQLSERVER", "ORACLE"])
async def test_new_relational_connection_crud(client, kind):
    payload = {
        "name": kind,
        "provider": kind,
        "config": {"host": "db", "database_name": "inventory", "username": "cdc"},
        "credentials": {"password": "private-source-password"},
    }
    response = await client.post("/api/v1/connections", json=payload)
    assert response.status_code == 201 and "private-source-password" not in response.text
    identifier = response.json()["id"]
    assert response.json()["capabilities"] == ["SOURCE"]
    payload["credentials"] = {}
    payload["name"] = kind + "-renamed"
    assert (await client.put(f"/api/v1/connections/{identifier}", json=payload)).status_code == 200
    assert (await client.get(f"/api/v1/sources/{identifier}")).status_code == 200
    assert (await client.delete(f"/api/v1/connections/{identifier}")).status_code == 200


@pytest.mark.parametrize("kind,port", [("mariadb", 3306), ("sqlserver", 1433), ("oracle", 1521)])
async def test_legacy_new_source_creation_also_uses_secure_defaults(client, kind, port):
    response = await client.post(
        "/api/v1/sources",
        json={
            "name": "legacy-source",
            "type": kind,
            "host": "db",
            "database_name": "inventory",
            "username": "cdc",
            "password": "private",
        },
    )
    assert response.status_code == 201
    assert response.json()["port"] == port
    assert response.json()["ssl_enabled"] is True
    assert "private" not in response.text


async def test_experimental_connector_plugin_guard_and_uri_safe_errors():
    import httpx
    import respx

    from app.adapters.connect import KafkaConnectClient
    from app.core.errors import DomainError

    cfg = {
        "connector.class": "io.debezium.connector.mongodb.MongoDbConnector",
        "mongodb.connection.string": "mongodb://user:private-password@db/",
    }
    with respx.mock:
        plugins = respx.get("http://connect/connector-plugins")
        plugins.mock(return_value=httpx.Response(200, json=[]))
        with pytest.raises(DomainError) as error:
            await KafkaConnectClient("http://connect").validate(cfg)
        assert error.value.code == "SOURCE_PLUGIN_MISSING"
        plugins.mock(
            return_value=httpx.Response(
                200, json=[{"class": cfg["connector.class"], "version": "3.3.2.Final"}]
            )
        )
        respx.put(
            "http://connect/connector-plugins/" + cfg["connector.class"] + "/config/validate"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "error_count": 1,
                    "configs": [
                        {
                            "value": {
                                "name": "mongodb.connection.string",
                                "errors": [cfg["mongodb.connection.string"]],
                            }
                        }
                    ],
                },
            )
        )
        with pytest.raises(DomainError) as error:
            await KafkaConnectClient("http://connect").validate(cfg)
        assert "private-password" not in str(error.value.details)


@pytest.mark.parametrize("kind", ["mariadb", "sqlserver", "mongodb", "oracle"])
async def test_new_source_pipeline_api_retains_identity_and_secrets(
    client, db_factory, monkeypatch, kind
):
    from unittest.mock import AsyncMock
    from uuid import UUID

    from sqlalchemy import select

    from app.models.entities import ConnectCluster, KafkaCluster, PipelineTable, SourceTable
    from app.providers.source_capabilities import source_topic

    config = {"database_name": "inventory"}
    credentials = {"connection_uri": "mongodb://user:private@db/"}
    if kind != "mongodb":
        config.update(host="db", username="cdc")
        credentials = {"password": "private"}
    created = await client.post(
        "/api/v1/connections",
        json={"name": kind, "provider": kind.upper(), "config": config, "credentials": credentials},
    )
    assert created.status_code == 201
    source_id = UUID(created.json()["id"])
    namespace, table_name = ("dbo" if kind == "sqlserver" else "inventory"), "orders"
    keys = ["_id"] if kind == "mongodb" else ["id"]
    columns = [
        {
            "name": keys[0],
            "type": "BSON" if kind == "mongodb" else "int",
            "nullable": False,
            "ordinal": 1,
        }
    ]
    properties = {"binlog_row_image": "FULL"} if kind == "mariadb" else {}
    kafka_id, connect_id = uuid4(), uuid4()
    async with db_factory() as session:
        session.add(KafkaCluster(id=kafka_id, name="kafka", bootstrap_servers="kafka:9092"))
        await session.flush()
        session.add(
            ConnectCluster(
                id=connect_id,
                name="connect",
                base_url="http://connect:8083",
                kafka_cluster_id=kafka_id,
            )
        )
        session.add(
            SourceTable(
                source_id=source_id,
                schema_name=namespace,
                table_name=table_name,
                primary_key_columns=keys,
                columns_json=columns,
                indexes_json=[],
                source_properties_json=properties,
                cdc_ready=True,
                cdc_status="READY",
                cdc_issues=[],
            )
        )
        await session.commit()
    database = AsyncMock()
    database.readiness.return_value = {"success": True}
    database.inspect_table.return_value = {
        "ready": True,
        "primary_key_columns": keys,
        "columns": columns,
        "indexes": [],
        "source_properties": properties,
        "estimated_rows": None,
    }
    monkeypatch.setattr("app.services.pipeline.source_adapter", AsyncMock(return_value=database))
    monkeypatch.setattr(
        "app.adapters.connect.KafkaConnectClient.validate", AsyncMock(return_value={"valid": True})
    )
    payload = {
        "name": "capture",
        "source_id": str(source_id),
        "kafka_cluster_id": str(kafka_id),
        "connect_cluster_id": str(connect_id),
        "topic_prefix": "capture",
        "tables": [
            {"schema_name": namespace, "table_name": table_name, "sync_mode": "CHANGE_EVENT_LOG"}
        ],
    }
    preview = await client.post("/api/v1/pipelines/preview", json=payload)
    assert preview.status_code == 200 and "private" not in preview.text
    expected_topic = source_topic(source(kind), "capture", namespace, table_name)
    assert preview.json()["topics"] == [expected_topic]
    response = await client.post("/api/v1/pipelines", json=payload)
    assert response.status_code == 201, response.text
    async with db_factory() as session:
        table = await session.scalar(select(PipelineTable))
        assert table.topic_name == expected_topic and table.effective_key_columns == keys
    if kind == "mongodb":
        payload["tables"][0]["sync_mode"] = "CURRENT_STATE"
        assert (await client.post("/api/v1/pipelines/preview", json=payload)).status_code == 422
