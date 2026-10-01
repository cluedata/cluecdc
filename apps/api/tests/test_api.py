import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import DomainError
from app.models.entities import SecretReference, SourceTable
from app.services.secrets import EncryptedDatabaseSecretProvider


async def test_health_aliases(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ok"}


async def test_source_secrets_audit_and_validation(client, db_factory, source_payload):
    response = await client.post("/api/v1/sources", json=source_payload)
    assert response.status_code == 201
    assert "password" not in response.text
    assert "secret_ref" not in response.text
    assert response.headers["X-Correlation-ID"]
    async with db_factory() as db:
        secret = await db.scalar(select(SecretReference))
        assert source_payload["password"] not in secret.ciphertext
        assert (await EncryptedDatabaseSecretProvider(db).get_secret(secret.id))[
            "password"
        ] == source_payload["password"]
    audit = await client.get("/api/v1/audit")
    assert "source.created" in audit.text
    assert source_payload["password"] not in audit.text
    invalid = await client.post(
        "/api/v1/sources", json={**source_payload, "port": "private-password"}
    )
    assert invalid.status_code == 422
    assert "private-password" not in invalid.text


async def test_internal_resolver_requires_machine_identity(client, db_factory, source_payload):
    await client.post("/api/v1/sources", json=source_payload)
    async with db_factory() as db:
        secret = await db.scalar(select(SecretReference))
        reference = str(secret.id)
    assert (await client.get(f"/internal/secrets/{reference}")).status_code == 403
    response = await client.get(
        f"/internal/secrets/{reference}",
        headers={
            "Authorization": "Bearer " + get_settings().connect_secret_token.get_secret_value()
        },
    )
    assert response.status_code == 200
    assert response.text == source_payload["password"]
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get(f"/api/v1/internal/secrets/{reference}")).status_code == 404


async def test_unknown_pipeline_and_duplicate_source(client, source_payload):
    assert (await client.get(f"/api/v1/pipelines/{uuid4()}")).status_code == 404
    assert (await client.post("/api/v1/sources", json=source_payload)).status_code == 201
    assert (await client.post("/api/v1/sources", json=source_payload)).status_code == 409


async def test_source_edit_rotates_secret_and_invalidates_discovery(
    client, db_factory, source_payload
):
    from uuid import UUID

    source = (await client.post("/api/v1/sources", json=source_payload)).json()
    async with db_factory() as db:
        db.add(
            SourceTable(
                source_id=UUID(source["id"]),
                schema_name="public",
                table_name="old_table",
                cdc_ready=True,
            )
        )
        await db.commit()
    response = await client.put(
        f"/api/v1/sources/{source['id']}",
        json={
            **source_payload,
            "database_name": "new_database",
            "password": "new-private-password",
        },
    )
    assert response.status_code == 200
    assert "new-private-password" not in response.text
    assert (await client.get(f"/api/v1/sources/{source['id']}/tables")).json() == []
    async with db_factory() as db:
        secrets = (await db.scalars(select(SecretReference))).all()
        assert len(secrets) == 1
        assert (await EncryptedDatabaseSecretProvider(db).get_secret(secrets[0].id))[
            "password"
        ] == "new-private-password"
    assert (await client.delete(f"/api/v1/sources/{source['id']}")).status_code == 200


async def test_source_unreachable_is_persisted(client, source_payload, monkeypatch):
    source = (await client.post("/api/v1/sources", json=source_payload)).json()

    async def failed(self):
        raise DomainError("SOURCE_UNAVAILABLE", "Source is unavailable", 503)

    monkeypatch.setattr("app.adapters.postgres.PostgresAdapter.test", failed)
    response = await client.post(f"/api/v1/sources/{source['id']}/test")
    assert response.status_code == 503
    assert (await client.get(f"/api/v1/sources/{source['id']}")).json()["status"] == "UNHEALTHY"
    assert "source.health_failed" in (await client.get("/api/v1/audit")).text


async def test_viewer_cannot_mutate_or_read_audit(client, monkeypatch, source_payload):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "token")
    token = "test-viewer"
    monkeypatch.setattr(
        settings,
        "auth_tokens_json",
        json.dumps(
            {hashlib.sha256(token.encode()).hexdigest(): {"actor": "viewer", "role": "Viewer"}}
        ),
    )
    assert (await client.get("/api/v1/sources")).status_code == 401
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/sources", headers=headers)).status_code == 200
    assert (
        await client.post("/api/v1/sources", json=source_payload, headers=headers)
    ).status_code == 403
    assert (await client.get("/api/v1/audit", headers=headers)).status_code == 403
    assert (
        await client.post(f"/api/v1/pipelines/{uuid4()}/prepare-topics", headers=headers)
    ).status_code == 403


async def setup_pipeline(client, db_factory, source_payload):
    source = (await client.post("/api/v1/sources", json=source_payload)).json()
    async with db_factory() as db:
        from uuid import UUID

        db.add(
            SourceTable(
                source_id=UUID(source["id"]),
                schema_name="public",
                table_name="customers",
                primary_key_columns=["id"],
                cdc_ready=True,
            )
        )
        await db.commit()
    kafka = (
        await client.post(
            "/api/v1/kafka/clusters", json={"name": "kafka", "bootstrap_servers": "kafka:9092"}
        )
    ).json()
    connect = (
        await client.post(
            "/api/v1/connect/clusters",
            json={
                "name": "connect",
                "base_url": "http://connect:8083",
                "kafka_cluster_id": kafka["id"],
            },
        )
    ).json()
    return {
        "name": "pipeline",
        "source_id": source["id"],
        "kafka_cluster_id": kafka["id"],
        "connect_cluster_id": connect["id"],
        "topic_prefix": "capture",
        "tables": [{"schema_name": "public", "table_name": "customers"}],
    }


async def test_invalid_config_does_not_persist(client, db_factory, source_payload, respx_mock):
    data = await setup_pipeline(client, db_factory, source_payload)
    respx_mock.put(
        "http://connect:8083/connector-plugins/io.debezium.connector.postgresql.PostgresConnector/config/validate"
    ).mock(return_value=httpx.Response(200, json={"error_count": 1, "configs": []}))
    response = await client.post("/api/v1/pipelines", json=data)
    assert response.status_code == 422
    assert (await client.get("/api/v1/pipelines")).json() == []


async def test_deployment_failure_state_lifecycle_and_delete(
    client, db_factory, source_payload, respx_mock, monkeypatch
):
    ensure = AsyncMock(return_value={"topics": ["capture.public.customers"], "created": []})
    monkeypatch.setattr("app.adapters.kafka.KafkaExplorer.ensure_topics", ensure)
    data = await setup_pipeline(client, db_factory, source_payload)
    respx_mock.put(
        "http://connect:8083/connector-plugins/io.debezium.connector.postgresql.PostgresConnector/config/validate"
    ).mock(return_value=httpx.Response(200, json={"error_count": 0}))
    preview = await client.post("/api/v1/pipelines/preview", json=data)
    assert source_payload["password"] not in preview.text
    p = (await client.post("/api/v1/pipelines", json=data)).json()
    name = "cluecdc-source-" + p["id"].replace("-", "")
    create = respx_mock.post("http://connect:8083/connectors").mock(
        return_value=httpx.Response(201, json={"name": name})
    )
    assert (await client.post(f"/api/v1/pipelines/{p['id']}/deploy")).status_code == 200
    assert create.called
    assert ensure.await_args.args[1] == ["capture.public.customers"]
    status = respx_mock.get(f"http://connect:8083/connectors/{name}/status")
    status.mock(
        return_value=httpx.Response(
            200,
            json={
                "connector": {"state": "RUNNING"},
                "tasks": [{"id": 0, "state": "FAILED", "trace": source_payload["password"]}],
            },
        )
    )
    observed = await client.get(f"/api/v1/pipelines/{p['id']}/status")
    assert observed.json()["actual_state"] == "DEGRADED"
    assert observed.json()["desired_state"] == "RUNNING"
    assert source_payload["password"] not in observed.text
    assert "DEGRADED" in (await client.get("/api/v1/operations/errors")).text
    respx_mock.put(f"http://connect:8083/connectors/{name}/pause").mock(
        return_value=httpx.Response(202)
    )
    status.mock(
        return_value=httpx.Response(200, json={"connector": {"state": "PAUSED"}, "tasks": []})
    )
    assert (await client.post(f"/api/v1/pipelines/{p['id']}/pause")).json()[
        "desired_state"
    ] == "PAUSED"
    respx_mock.delete(f"http://connect:8083/connectors/{name}").mock(
        return_value=httpx.Response(204)
    )
    assert (await client.delete(f"/api/v1/pipelines/{p['id']}")).status_code == 200
    audit = (await client.get("/api/v1/audit")).text
    assert (
        "pipeline.deployed" in audit and "pipeline.paused" in audit and "pipeline.deleted" in audit
    )
    assert source_payload["password"] not in audit


async def test_unavailable_connect_rejects_pipeline(client, db_factory, source_payload, respx_mock):
    data = await setup_pipeline(client, db_factory, source_payload)
    respx_mock.put(
        "http://connect:8083/connector-plugins/io.debezium.connector.postgresql.PostgresConnector/config/validate"
    ).mock(side_effect=httpx.ConnectError("refused"))
    assert (await client.post("/api/v1/pipelines", json=data)).status_code == 503
    assert (await client.get("/api/v1/pipelines")).json() == []
