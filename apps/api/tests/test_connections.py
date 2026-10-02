from uuid import UUID

from app.models.entities import Connection, Destination, Source


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


async def test_database_connection_is_canonical_and_materializes_runtime_adapters(
    client, db_factory
):
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
        assert source is not None and destination is not None
        assert source.id == destination.id

    updated_payload = database_payload() | {
        "name": "Commerce PostgreSQL Updated",
        "capabilities": ["SOURCE", "DESTINATION"],
        "config": database_payload()["config"] | {"host": "postgres.internal", "ssl_enabled": True},
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


async def test_provider_catalog_only_exposes_working_database_providers(client):
    providers = (await client.get("/api/v1/connection-providers")).json()
    assert {item["provider"] for item in providers} == {"POSTGRESQL", "MYSQL"}
    assert {item["category"] for item in providers} == {"DATABASE"}
