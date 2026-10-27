import hashlib
import json
from uuid import UUID

from app.core.config import get_settings
from app.models.entities import (
    Base,
    ConnectCluster,
    Connection,
    Destination,
    KafkaCluster,
    Pipeline,
    PipelineDestination,
    SecretReference,
    Source,
)
from app.services.secrets import EncryptedDatabaseSecretProvider


def database_payload() -> dict:
    return {
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
    }


async def test_database_connection_is_the_only_persisted_endpoint(client, db_factory):
    response = await client.post("/api/v1/connections", json=database_payload())
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
        assert source is destination
        assert source is not None
        assert "sources" not in Base.metadata.tables
        assert "destinations" not in Base.metadata.tables
        secret_ref = source.secret_ref
        original_ciphertext = (await db.get(SecretReference, secret_ref)).ciphertext

    rotated_payload = database_payload() | {"credentials": {"password": "rotated-private-password"}}
    rotated = await client.put(f"/api/v1/connections/{value['id']}", json=rotated_payload)
    assert rotated.status_code == 200
    assert "rotated-private-password" not in rotated.text
    async with db_factory() as db:
        source = await db.get(Source, UUID(value["id"]))
        assert source.secret_ref == secret_ref
        stored = await db.get(SecretReference, secret_ref)
        assert stored.ciphertext != original_ciphertext
        assert "rotated-private-password" not in stored.ciphertext
        assert await EncryptedDatabaseSecretProvider(db).get_secret(secret_ref) == {
            "password": "rotated-private-password"
        }

    updated_payload = database_payload() | {
        "name": "Commerce PostgreSQL Updated",
        "capabilities": ["SOURCE", "DESTINATION"],
        "config": database_payload()["config"] | {"host": "postgres.internal", "ssl_enabled": True},
        "credentials": {},
    }
    updated = await client.put(f"/api/v1/connections/{value['id']}", json=updated_payload)
    assert updated.status_code == 200
    assert updated.json()["config"]["host"] == "postgres.internal"
    audit = await client.get(f"/api/v1/audit?resource_id={value['id']}")
    assert "private-password" not in audit.text
    assert "rotated-private-password" not in audit.text

    removed = await client.delete(f"/api/v1/connections/{value['id']}")
    assert removed.status_code == 200
    async with db_factory() as db:
        identifier = UUID(value["id"])
        assert await db.get(Source, identifier) is None
        assert await db.get(Destination, identifier) is None
        assert await db.get(Connection, identifier) is None


async def test_provider_catalog_exposes_database_and_object_storage_providers(client):
    providers = (await client.get("/api/v1/connection-providers")).json()
    assert {item["provider"] for item in providers} == {"POSTGRESQL", "MYSQL", "AWS_S3", "MINIO"}
    assert {item["category"] for item in providers} == {"DATABASE", "OBJECT_STORAGE"}


async def test_in_use_connection_reports_every_dependency_until_last_reference_is_removed(
    client, db_factory
):
    connection = (await client.post("/api/v1/connections", json=database_payload())).json()
    identifier = UUID(connection["id"])
    async with db_factory() as db:
        kafka = KafkaCluster(
            name="dependency-kafka",
            bootstrap_servers="kafka:29092",
            security_protocol="PLAINTEXT",
        )
        db.add(kafka)
        await db.flush()
        connect = ConnectCluster(
            name="dependency-connect",
            base_url="http://connect:8083",
            kafka_cluster_id=kafka.id,
        )
        db.add(connect)
        await db.flush()
        pipeline = Pipeline(
            name="orders-cdc",
            source_id=identifier,
            kafka_cluster_id=kafka.id,
            connect_cluster_id=connect.id,
            topic_prefix="orders_cdc",
            snapshot_mode="initial",
            desired_state="PAUSED",
            actual_state="PAUSED",
        )
        db.add(pipeline)
        await db.flush()
        deliveries = [
            PipelineDestination(
                pipeline_id=pipeline.id,
                destination_id=identifier,
                name=name,
                desired_state=desired,
                actual_state=actual,
            )
            for name, desired, actual in [
                ("analytics-postgres", "RUNNING", "FAILED"),
                ("reporting-postgres", "PAUSED", "PAUSED"),
            ]
        ]
        db.add_all(deliveries)
        await db.commit()
        delivery_ids = [item.id for item in deliveries]
        pipeline_id = pipeline.id

    blocked = await client.delete(f"/api/v1/connections/{identifier}")
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "CONNECTION_IN_USE"
    assert {(item["type"], item["name"]) for item in error["details"]["used_by"]} == {
        ("pipeline", "orders-cdc"),
        ("delivery", "analytics-postgres"),
        ("delivery", "reporting-postgres"),
    }
    assert (await client.get(f"/api/v1/connections/{identifier}")).status_code == 200

    async with db_factory() as db:
        await db.delete(await db.get(PipelineDestination, delivery_ids[0]))
        await db.commit()
    still_blocked = await client.delete(f"/api/v1/connections/{identifier}")
    assert still_blocked.status_code == 409
    names = {item["name"] for item in still_blocked.json()["error"]["details"]["used_by"]}
    assert names == {"orders-cdc", "reporting-postgres"}

    async with db_factory() as db:
        await db.delete(await db.get(PipelineDestination, delivery_ids[1]))
        await db.delete(await db.get(Pipeline, pipeline_id))
        await db.commit()
    assert (await client.delete(f"/api/v1/connections/{identifier}")).status_code == 200


async def test_connection_delete_remains_admin_only(client, monkeypatch):
    connection = (await client.post("/api/v1/connections", json=database_payload())).json()
    settings = get_settings()
    token = "connection-viewer"
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(
        settings,
        "auth_tokens_json",
        json.dumps(
            {hashlib.sha256(token.encode()).hexdigest(): {"actor": "viewer", "role": "Viewer"}}
        ),
    )
    response = await client.delete(
        f"/api/v1/connections/{connection['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
