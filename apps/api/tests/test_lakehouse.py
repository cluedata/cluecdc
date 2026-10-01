from unittest.mock import AsyncMock
from uuid import UUID

from sqlalchemy import select

from app.adapters.connect import KafkaConnectClient
from app.models.entities import (
    ConnectCluster,
    Connection,
    Connector,
    Destination,
    KafkaCluster,
    LakehouseDestination,
    Pipeline,
    PipelineTable,
    SecretReference,
    Source,
    SourceTable,
)
from app.schemas.lakehouse import IcebergDeliveryInput
from app.services.iceberg_types import iceberg_type
from app.services.lakehouse import ICEBERG_SINK_CLASS, build_connector_config
from app.services.secrets import EncryptedDatabaseSecretProvider


def storage_payload(name: str = "MinIO Production") -> dict:
    return {
        "name": name,
        "category": "OBJECT_STORAGE",
        "provider": "MINIO",
        "description": "Physical Lakehouse files",
        "config": {
            "endpoint": "http://minio:9000",
            "region": "us-east-1",
            "bucket": "lakehouse",
            "base_path": "warehouse",
            "path_style_access": True,
            "ssl_enabled": False,
        },
        "credentials": {"access_key": "minio-user", "secret_key": "minio-private"},
    }


async def test_connection_crud_masks_secrets_and_protects_dependencies(client, db_factory):
    storage_response = await client.post("/api/v1/connections", json=storage_payload())
    assert storage_response.status_code == 201
    assert "minio-private" not in storage_response.text
    storage = storage_response.json()
    assert storage["credentials"]["configured"] is True
    assert storage["credentials"]["masked_value"] == "••••••••••••"

    lakehouse = await client.post(
        "/api/v1/lakehouse-destinations",
        json={
            "name": "Production Lakehouse",
            "storage_connection_id": storage["id"],
            "warehouse": "s3://lakehouse/warehouse",
            "namespace": "production",
        },
    )
    assert lakehouse.status_code == 201
    assert "catalog_connection_id" not in lakehouse.json()
    assert "query_engine_connection_id" not in lakehouse.json()
    blocked = await client.delete(f"/api/v1/connections/{storage['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["used_by"][0]["name"] == "Production Lakehouse"
    async with db_factory() as db:
        values = (await db.scalars(select(SecretReference))).all()
        decrypted = [
            await EncryptedDatabaseSecretProvider(db).get_secret(item.id) for item in values
        ]
        assert any(value.get("secret_key") == "minio-private" for value in decrypted)


async def test_structured_connection_test_updates_health(client, monkeypatch):
    created = (await client.post("/api/v1/connections", json=storage_payload())).json()

    class HealthyProvider:
        async def test_connection(self):
            return {
                "success": True,
                "checks": [
                    {"name": "authentication", "status": "success"},
                    {"name": "bucket_access", "status": "success"},
                    {"name": "write_permission", "status": "success"},
                ],
            }

    monkeypatch.setattr(
        "app.services.connections.get_connection_provider",
        lambda connection, credentials: HealthyProvider(),
    )
    result = await client.post(f"/api/v1/connections/{created['id']}/test")
    assert result.status_code == 200
    assert len(result.json()["checks"]) == 3
    detail = (await client.get(f"/api/v1/connections/{created['id']}")).json()
    assert detail["status"] == "HEALTHY" and detail["last_tested_at"]


async def test_database_connection_is_canonical_and_materializes_runtime_adapters(
    client, db_factory
):
    response = await client.post(
        "/api/v1/connections",
        json={
            "name": "Commerce PostgreSQL",
            "category": "DATABASE",
            "provider": "POSTGRESQL",
            "config": {
                "host": "postgres",
                "port": 5432,
                "database_name": "commerce",
                "username": "cluecdc",
                "ssl_enabled": False,
                "environment": "PROD",
            },
            "credentials": {"password": "private-password"},
        },
    )
    assert response.status_code == 201
    value = response.json()
    assert value["type"] == "POSTGRESQL"
    assert value["capabilities"] == ["SOURCE", "DESTINATION"]
    assert "private-password" not in response.text

    source_view = await client.get("/api/v1/connections?capability=SOURCE")
    destination_view = await client.get("/api/v1/connections?capability=DESTINATION")
    assert [item["id"] for item in source_view.json()] == [value["id"]]
    assert [item["id"] for item in destination_view.json()] == [value["id"]]

    async with db_factory() as db:
        identifier = UUID(value["id"])
        source = await db.get(Source, identifier)
        destination = await db.get(Destination, identifier)
        assert source is not None and destination is not None
        assert source.id == destination.id

    updated_payload = {
        "name": "Commerce PostgreSQL Updated",
        "category": "DATABASE",
        "provider": "POSTGRESQL",
        "capabilities": ["SOURCE", "DESTINATION"],
        "config": {
            "host": "postgres.internal",
            "port": 5432,
            "database_name": "commerce",
            "username": "cluecdc",
            "ssl_enabled": True,
            "environment": "PROD",
        },
        "credentials": {},
    }
    updated = await client.put(f"/api/v1/connections/{value['id']}", json=updated_payload)
    assert updated.status_code == 200
    assert updated.json()["config"]["host"] == "postgres.internal"

    removed = await client.delete(f"/api/v1/connections/{value['id']}")
    assert removed.status_code == 200
    async with db_factory() as db:
        identifier = UUID(value["id"])
        assert await db.get(Source, identifier) is None
        assert await db.get(Destination, identifier) is None
        assert await db.get(Connection, identifier) is None


async def test_provider_catalog_only_exposes_s3_and_minio_object_storage(client):
    await client.post("/api/v1/connections", json=storage_payload())
    unsupported = await client.post(
        "/api/v1/connections",
        json={
            "name": "Unsupported Query Engine",
            "category": "QUERY_ENGINE",
            "provider": "TRINO",
            "config": {},
        },
    )
    assert unsupported.status_code == 422
    values = (await client.get("/api/v1/connections")).json()
    assert {item["type"] for item in values} == {"MINIO"}
    providers = (await client.get("/api/v1/connection-providers")).json()
    object_storage = {
        item["provider"] for item in providers if item["category"] == "OBJECT_STORAGE"
    }
    assert object_storage == {"AWS_S3", "MINIO"}
    assert {item["category"] for item in providers} == {"DATABASE", "OBJECT_STORAGE"}


async def test_iceberg_config_uses_existing_delivery_runtime_and_secret_references(
    db_factory, monkeypatch
):
    async with db_factory() as db:
        secrets = EncryptedDatabaseSecretProvider(db)
        source_secret = await secrets.put_secret({"password": "source-private"})
        storage_secret = await secrets.put_secret(
            {"access_key": "minio-user", "secret_key": "minio-private"}
        )
        storage = Connection(
            name="MinIO",
            category="OBJECT_STORAGE",
            provider="MINIO",
            config_json={
                "endpoint": "http://minio:9000",
                "region": "us-east-1",
                "bucket": "lakehouse",
                "base_path": "warehouse",
                "path_style_access": True,
                "ssl_enabled": False,
            },
            secret_ref=storage_secret,
        )
        source = Source(
            name="Postgres",
            type="postgresql",
            environment="test",
            host="postgres",
            port=5432,
            database_name="commerce",
            username="cdc",
            secret_ref=source_secret,
        )
        kafka = KafkaCluster(name="Kafka", bootstrap_servers="kafka:29092")
        db.add_all([storage, source, kafka])
        await db.flush()
        connect = ConnectCluster(
            name="Connect", base_url="http://connect:8083", kafka_cluster_id=kafka.id
        )
        db.add(connect)
        await db.flush()
        capture = Connector(
            name="capture",
            connector_type="source",
            connect_cluster_id=connect.id,
            connector_class="io.debezium.connector.postgresql.PostgresConnector",
            config_json={},
        )
        db.add(capture)
        await db.flush()
        pipeline = Pipeline(
            name="Customer CDC",
            source_id=source.id,
            kafka_cluster_id=kafka.id,
            connect_cluster_id=connect.id,
            connector_id=capture.id,
            topic_prefix="postgres",
            snapshot_mode="initial",
        )
        destination = LakehouseDestination(
            name="Production Lakehouse",
            storage_connection_id=storage.id,
            warehouse="s3://lakehouse/warehouse",
            namespace="production",
        )
        db.add_all([pipeline, destination])
        await db.flush()
        db.add_all(
            [
                SourceTable(
                    source_id=source.id,
                    schema_name="public",
                    table_name="customers",
                    primary_key_columns=["id"],
                    columns_json=[
                        {"name": "id", "type": "bigint", "nullable": False},
                        {"name": "email", "type": "varchar(255)", "nullable": True},
                    ],
                ),
                PipelineTable(
                    pipeline_id=pipeline.id,
                    schema_name="public",
                    table_name="customers",
                    topic_name="postgres.public.customers",
                    primary_key_columns=["id"],
                ),
            ]
        )
        await db.commit()
        monkeypatch.setattr(
            KafkaConnectClient,
            "plugins",
            AsyncMock(return_value=[{"class": ICEBERG_SINK_CLASS, "type": "sink"}]),
        )
        monkeypatch.setattr(KafkaConnectClient, "validate", AsyncMock(return_value={"valid": True}))
        config, cluster, metadata = await build_connector_config(
            db,
            destination,
            IcebergDeliveryInput(pipeline_id=pipeline.id),
            "iceberg-customers",
        )
        assert cluster.id == connect.id
        assert config["transforms.debezium.type"].endswith("DebeziumTransform")
        assert config["iceberg.tables.cdc-field"] == "_cdc.op"
        assert config["iceberg.tables.upsert-mode-enabled"] == "true"
        assert config["consumer.override.group.id"] == "iceberg-customers"
        assert config["iceberg.catalog.type"] == "hadoop"
        assert config["iceberg.catalog.warehouse"] == "s3://lakehouse/warehouse"
        assert config["iceberg.table.production.customers.id-columns"] == "id"
        assert "minio-private" not in str(config)
        assert "${cluecdc:" in config["iceberg.catalog.s3.secret-access-key"]
        assert metadata[0]["columns"][0]["iceberg_type"] == "long"


def test_iceberg_schema_mapping_is_explicit():
    assert iceberg_type("boolean") == "boolean"
    assert iceberg_type("numeric(20,4)") == "decimal(20,4)"
    assert iceberg_type("timestamp with time zone") == "timestamptz"
    assert iceberg_type("jsonb") == "string"
