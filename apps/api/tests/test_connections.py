from uuid import UUID

from app.models.entities import Base, Connection, Destination, SecretReference, Source
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
