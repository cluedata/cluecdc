from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models.entities import AuditLog, Connection, SecretReference
from app.services import connections as service
from app.services.secrets import EncryptedDatabaseSecretProvider


def payload(provider="POSTGRESQL"):
    return {
        "name": "Draft connection",
        "provider": provider,
        "category": "DATABASE",
        "capabilities": ["SOURCE"],
        "config": {
            "host": "database.internal",
            "port": 5432 if provider == "POSTGRESQL" else 3306,
            "database_name": "fixture",
            "username": "source_user",
        },
        "credentials": {"password": "draft-password"},
    }


@pytest.mark.parametrize("provider", ["POSTGRESQL", "MYSQL"])
async def test_source_draft_tests_current_values_without_creating_resources(
    client, db_factory, monkeypatch, provider
):
    seen = []

    async def run(connection, credentials):
        seen.append((connection, credentials))
        return {"status": "HEALTHY"}

    monkeypatch.setattr(service, "_run_test", run)
    data = payload(provider)
    response = await client.post(
        "/api/v1/sources/test-connection",
        json={
            "name": data["name"],
            "type": provider.lower(),
            **data["config"],
            "password": "draft-password",
        },
    )
    assert response.status_code == 200
    assert seen[0][0].config_json["host"] == "database.internal"
    assert seen[0][0].capabilities_json == ["SOURCE"]
    assert seen[0][1] == {"password": "draft-password"}
    assert "draft-password" not in response.text
    async with db_factory() as db:
        for model in (Connection, SecretReference, AuditLog):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("provider", ["POSTGRESQL", "MYSQL", "AWS_S3", "MINIO"])
async def test_edited_connection_tests_draft_and_retains_saved_secrets_without_writes(
    client, db_factory, monkeypatch, provider
):
    data = payload(provider)
    if provider in {"AWS_S3", "MINIO"}:
        data.update(
            category="OBJECT_STORAGE",
            capabilities=["DESTINATION"],
            config={
                "bucket": "test-bucket",
                "endpoint": "http://minio:9000",
                "use_ssl": False,
                "path_style_access": True,
            },
            credentials={"access_key": "private-access", "secret_key": "private-secret"},
        )
    created = await client.post("/api/v1/connections", json=data)
    assert created.status_code == 201
    identifier = created.json()["id"]
    original = (await client.get(f"/api/v1/connections/{identifier}")).json()
    seen = []

    async def run(connection, credentials):
        seen.append((connection, credentials))
        return {"success": True, "checks": []}

    monkeypatch.setattr(service, "_run_test", run)
    draft = data | {"name": "Unsaved new name", "credentials": {}}
    draft["config"] = data["config"] | (
        {"host": "edited.internal"} if provider in {"POSTGRESQL", "MYSQL"} else {"prefix": "edited"}
    )
    tested = await client.post(f"/api/v1/connections/test?identifier={identifier}", json=draft)
    assert tested.status_code == 200
    assert seen[0][0].name == "Unsaved new name"
    assert all(seen[0][0].config_json[key] == value for key, value in draft["config"].items())
    assert seen[0][1] == data["credentials"]
    assert (await client.get(f"/api/v1/connections/{identifier}")).json() == original

    replacement = (
        "replacement-password" if provider in {"POSTGRESQL", "MYSQL"} else "replacement-secret"
    )
    key = "password" if provider in {"POSTGRESQL", "MYSQL"} else "secret_key"
    draft["credentials"] = {key: replacement}
    result = await client.post(f"/api/v1/connections/test?identifier={identifier}", json=draft)
    assert result.status_code == 200
    assert seen[1][1] == data["credentials"] | {key: replacement}
    assert replacement not in result.text
    async with db_factory() as db:
        saved = await db.get(Connection, UUID(identifier))
        assert (
            await EncryptedDatabaseSecretProvider(db).get_secret(saved.secret_ref)
            == data["credentials"]
        )
        assert await db.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.parametrize("role", ["sources", "destinations"])
async def test_database_edit_compatibility_routes_test_retained_password(client, monkeypatch, role):
    data = payload() | {"capabilities": ["SOURCE" if role == "sources" else "DESTINATION"]}
    identifier = (await client.post("/api/v1/connections", json=data)).json()["id"]

    async def run(connection, credentials):
        assert connection.config_json["host"] == "draft.internal"
        assert credentials == {"password": "draft-password"}
        return {"status": "HEALTHY"}

    monkeypatch.setattr(service, "_run_test", run)
    response = await client.post(
        f"/api/v1/{role}/test-connection?identifier={identifier}",
        json={
            "name": "Edited draft",
            "type": "postgresql",
            **data["config"],
            "host": "draft.internal",
        },
    )
    assert response.status_code == 200
    wrong_role = "destinations" if role == "sources" else "sources"
    assert (
        await client.post(
            f"/api/v1/{wrong_role}/test-connection?identifier={identifier}",
            json={"name": "Draft", **data["config"]},
        )
    ).status_code == 404


async def test_draft_requires_credentials_and_rejects_provider_changes(client, monkeypatch):
    data = payload()
    identifier = (await client.post("/api/v1/connections", json=data)).json()["id"]
    missing = await client.post("/api/v1/connections/test", json=data | {"credentials": {}})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "CONNECTION_CREDENTIALS_REQUIRED"
    changed = await client.post(
        f"/api/v1/connections/test?identifier={identifier}", json=payload("MYSQL")
    )
    assert changed.status_code == 422
    assert changed.json()["error"]["code"] == "CONNECTION_PROVIDER_IMMUTABLE"


async def test_failed_draft_does_not_change_saved_health_or_configuration(client, monkeypatch):
    from app.core.errors import DomainError

    identifier = (await client.post("/api/v1/connections", json=payload())).json()["id"]
    original = (await client.get(f"/api/v1/connections/{identifier}")).json()

    async def failed(connection, credentials):
        raise DomainError("SOURCE_AUTH_FAILED", "Authentication failed", 422)

    monkeypatch.setattr(service, "_run_test", failed)
    response = await client.post(
        f"/api/v1/connections/test?identifier={identifier}",
        json=payload() | {"credentials": {"password": "incorrect-password"}},
    )
    assert response.status_code == 422
    assert "incorrect-password" not in response.text
    assert (await client.get(f"/api/v1/connections/{identifier}")).json() == original


@pytest.mark.parametrize(
    "path", ["connections/test", "sources/test-connection", "destinations/test-connection"]
)
async def test_draft_connection_tests_require_write_permission(client, path):
    from app.core.auth import Principal, principal
    from app.main import app

    data = payload() if path == "connections/test" else {"name": "Draft", **payload()["config"]}
    app.dependency_overrides[principal] = lambda: Principal("viewer", "Viewer")
    try:
        assert (await client.post(f"/api/v1/{path}", json=data)).status_code == 403
    finally:
        app.dependency_overrides.pop(principal)
