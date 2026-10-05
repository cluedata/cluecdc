import json
import threading
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.core.errors import DomainError
from app.models.entities import Connection, Connector, SecretReference
from app.schemas.requests import DeliveryInput
from app.services.destinations.providers import S3_CLASS, ObjectStorageDeliveryProvider
from app.services.object_storage import test_connection as check_object_storage
from app.services.secrets import EncryptedDatabaseSecretProvider
from tests.test_destinations import capture  # noqa: F401 - shared deployed-capture fixture


def storage_payload(provider="MINIO"):
    return {
        "name": "CDC archive",
        "category": "OBJECT_STORAGE",
        "provider": provider,
        "config": {
            "bucket": "cdc-archive",
            "endpoint": "http://minio:9000",
            "prefix": "archive",
            "path_style_access": True,
        },
        "credentials": {
            "access_key": "private-test-access-key",
            "secret_key": "private-test-secret-key",
        },
    }


@pytest.mark.parametrize(
    "config",
    [
        {"endpoint": "http://private-test-access-key:private-test-secret-key@minio:9000"},
        {"endpoint": "ftp://minio:9000"},
        {"endpoint": "http://minio:9000/?secret=private-test-secret-key"},
        {"bucket": "invalid_bucket"},
        {"prefix": "archive/../escape"},
        {"tls_verify": False},
    ],
)
async def test_storage_validation_rejects_unsafe_configuration_without_echoing_secrets(
    client, config
):
    payload = storage_payload()
    payload["config"].update(config)
    response = await client.post("/api/v1/connections", json=payload)
    assert response.status_code == 422
    assert all(value not in response.text for value in payload["credentials"].values())


async def test_storage_credentials_are_encrypted_rotated_and_never_returned(client, db_factory):
    payload = storage_payload("AWS_S3")
    payload["credentials"]["session_token"] = "private-test-session-token"
    response = await client.post("/api/v1/connections", json=payload)
    assert response.status_code == 201
    identifier = response.json()["id"]
    assert response.json()["capabilities"] == ["DESTINATION"]
    async with db_factory() as db:
        connection = await db.get(Connection, UUID(identifier))
        secret = await db.get(SecretReference, connection.secret_ref)
        assert all(value not in secret.ciphertext for value in payload["credentials"].values())
        assert (
            await EncryptedDatabaseSecretProvider(db).get_secret(secret.id)
            == payload["credentials"]
        )
        old_reference = secret.id
    changed = {**payload, "credentials": {"secret_key": "rotated-test-secret"}}
    assert (await client.put(f"/api/v1/connections/{identifier}", json=changed)).status_code == 200
    async with db_factory() as db:
        connection = await db.get(Connection, UUID(identifier))
        assert connection.secret_ref == old_reference
        rotated = await EncryptedDatabaseSecretProvider(db).get_secret(old_reference)
        assert rotated == {**payload["credentials"], "secret_key": "rotated-test-secret"}
    for path in ("/connections", f"/connections/{identifier}", "/destinations", "/audit"):
        response = await client.get("/api/v1" + path)
        assert response.status_code == 200
        assert all(
            value not in response.text
            for value in [*payload["credentials"].values(), "rotated-test-secret"]
        )
    assert not (await client.get("/api/v1/connections?capability=SOURCE")).json()


async def test_storage_delivery_preserves_events_and_secret_references(
    client,
    capture,  # noqa: F811 - imported pytest fixture
    db_factory,
    respx_mock,
    monkeypatch,
):
    respx_mock.get("http://connect:8083/connector-plugins").mock(
        return_value=httpx.Response(
            200, json=[{"class": S3_CLASS, "type": "sink", "version": "3.4.2"}]
        )
    )
    respx_mock.put(f"http://connect:8083/connector-plugins/{S3_CLASS}/config/validate").mock(
        return_value=httpx.Response(200, json={"error_count": 0})
    )
    monkeypatch.setattr(
        "app.services.object_storage.test_connection",
        AsyncMock(return_value={"success": True, "checks": []}),
    )
    target = (await client.post("/api/v1/connections", json=storage_payload())).json()
    options = {
        "pipeline_id": capture["pipeline_id"],
        "delivery_type": "OBJECT_STORAGE",
        "topics": ["capture.public.customers"],
    }
    preview = await client.post(f"/api/v1/destinations/{target['id']}/preview", json=options)
    assert preview.status_code == 200, preview.text
    assert "transforms" not in preview.json()["config"]
    deployed = await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=options)
    assert deployed.status_code == 201, deployed.text
    delivery = deployed.json()
    assert delivery["delivery_type"] == "OBJECT_STORAGE"
    assert delivery["topic_mapping_json"] == [{"topic": "capture.public.customers"}]
    assert "primary_key_mode" not in delivery["configuration_json"]
    assert "auto_create" not in delivery["configuration_json"]
    assert delivery["configuration_json"]["compression"] == "gzip"
    async with db_factory() as db:
        connector = await db.scalar(select(Connector).where(Connector.connector_class == S3_CLASS))
        assert "private-test" not in json.dumps(connector.config_json)
    requests = [
        call.request
        for call in respx_mock.calls
        if call.request.method == "POST" and call.request.url.path == "/connectors"
    ]
    config = json.loads(requests[-1].content)["config"]
    assert config["aws.access.key.id"].startswith("${cluecdc:")
    assert config["aws.secret.access.key"].endswith(":secret_key}")
    assert config["format.output.envelope"] == "true"
    assert config["format.output.fields"] == "key,value,offset,timestamp"
    assert config["errors.tolerance"] == "none"
    assert config["file.name.template"].startswith("archive/{{topic}}/year=")
    for path in ("/deliveries", f"/deliveries/{delivery['id']}", "/connections", "/audit"):
        response = await client.get("/api/v1" + path)
        assert response.status_code == 200
        assert all(
            value not in response.text for value in storage_payload()["credentials"].values()
        )
    assert (await client.delete(f"/api/v1/connections/{target['id']}")).status_code == 409
    changed = storage_payload()
    changed["config"]["prefix"] = "different-prefix"
    assert (
        await client.put(f"/api/v1/connections/{target['id']}", json=changed)
    ).status_code == 409


def test_session_credentials_use_a_plugin_provider_and_never_jdbc_transforms():
    connection = Connection(
        name="AWS",
        category="OBJECT_STORAGE",
        provider="AWS_S3",
        config_json={"bucket": "cdc-archive", "region": "us-east-1"},
        secret_ref=uuid4(),
    )
    data = DeliveryInput(
        pipeline_id=uuid4(), delivery_type="OBJECT_STORAGE", topics=["capture.public.customers"]
    )
    config = ObjectStorageDeliveryProvider().build_config(
        connection, data, {}, "sink", has_session_token=True
    )
    assert config["aws.credentials.provider"] == "io.cluecdc.connect.ClueSessionCredentialsProvider"
    assert config["cluecdc.session.token"].endswith(":session_token}")
    assert "aws.access.key.id" not in config
    assert "transforms" not in config


@pytest.mark.parametrize("fail_at", [None, "put_object", "head_object", "delete_object"])
async def test_connection_probe_cleans_up_and_runs_outside_the_event_loop(monkeypatch, fail_at):
    client = MagicMock()
    thread = threading.get_ident()
    calls = []

    def put(**kwargs):
        calls.append((threading.get_ident(), kwargs["Key"]))
        if fail_at == "put_object":
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "private-test-secret-key"}},
                "put_object",
            )

    client.put_object.side_effect = put
    if fail_at and fail_at != "put_object":
        getattr(client, fail_at).side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private-test-secret-key"}}, fail_at
        )
    monkeypatch.setattr("app.services.object_storage._client", lambda *_: client)
    connection = Connection(
        name="probe",
        category="OBJECT_STORAGE",
        provider="MINIO",
        config_json={"bucket": "cdc-archive", "prefix": "archive"},
    )
    if fail_at:
        with pytest.raises(DomainError) as error:
            await check_object_storage(connection, storage_payload()["credentials"])
        assert "private-test-secret-key" not in error.value.message
    else:
        assert (await check_object_storage(connection, storage_payload()["credentials"]))["success"]
    assert calls[0][0] != thread
    assert calls[0][1].startswith("archive/.cluecdc/connection-tests/")
    client.delete_object.assert_called_once_with(Bucket="cdc-archive", Key=calls[0][1])
    client.close.assert_called_once()
