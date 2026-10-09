import hashlib
import json
import re
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import DomainError
from app.models.entities import (
    AuditLog,
    Connection,
    Connector,
    PipelineDestination,
    SecretReference,
    SourceTable,
)
from app.services.destinations.adapter import PostgresDestinationAdapter, supported_type
from app.services.destinations.config import JDBC_CLASS
from app.services.secrets import EncryptedDatabaseSecretProvider
from tests.test_api import setup_pipeline


@pytest.fixture
def target_payload():
    return {
        "name": "Analytics",
        "host": "target",
        "database_name": "analytics",
        "username": "delivery",
        "password": "private-destination-password",
        "environment": "DEV",
    }


@pytest.fixture
async def capture(client, db_factory, source_payload, respx_mock, monkeypatch):
    monkeypatch.setattr(
        "app.adapters.kafka.KafkaExplorer.ensure_topics",
        AsyncMock(return_value={"topics": ["capture.public.customers"], "created": []}),
    )
    data = await setup_pipeline(client, db_factory, source_payload)
    async with db_factory() as db:
        table = await db.scalar(select(SourceTable))
        table.columns_json = [
            {"name": "id", "type": "bigint", "nullable": False, "ordinal": 1},
            {"name": "name", "type": "text", "nullable": False, "ordinal": 2},
        ]
        await db.commit()
    respx_mock.put(
        "http://connect:8083/connector-plugins/io.debezium.connector.postgresql.PostgresConnector/config/validate"
    ).mock(return_value=httpx.Response(200, json={"error_count": 0}))
    respx_mock.post("http://connect:8083/connectors").mock(
        return_value=httpx.Response(201, json={"name": "created"})
    )
    pipeline = (await client.post("/api/v1/pipelines", json=data)).json()
    assert (await client.post(f"/api/v1/pipelines/{pipeline['id']}/deploy")).status_code == 200
    respx_mock.get("http://connect:8083/connector-plugins").mock(
        return_value=httpx.Response(
            200, json=[{"class": JDBC_CLASS, "type": "sink", "version": "3.3.1.Final"}]
        )
    )
    respx_mock.put(f"http://connect:8083/connector-plugins/{JDBC_CLASS}/config/validate").mock(
        return_value=httpx.Response(200, json={"error_count": 0})
    )

    async def topics(self, cluster, name=None):
        return [{"name": "capture.public.customers"}]

    async def test(self):
        return {"status": "HEALTHY", "version": "test"}

    async def validate(self, data, metadata):
        return None

    monkeypatch.setattr("app.adapters.kafka.KafkaExplorer.topics", topics)
    monkeypatch.setattr(PostgresDestinationAdapter, "test_connection", test)
    monkeypatch.setattr(PostgresDestinationAdapter, "validate", validate)
    return {
        "pipeline_id": pipeline["id"],
        "mappings": [
            {
                "topic": "capture.public.customers",
                "schema_name": "public",
                "table_name": "customers",
            }
        ],
    }


async def make_target(client, target_payload):
    response = await client.post("/api/v1/destinations", json=target_payload)
    assert response.status_code == 201
    assert target_payload["password"] not in response.text
    return response.json()


async def test_deliveries_are_first_class_views_of_existing_sink_connectors(
    client, capture, target_payload, db_factory
):
    target = await make_target(client, target_payload)
    deployed = await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=capture)
    assert deployed.status_code == 201
    delivery = deployed.json()

    inventory = (await client.get("/api/v1/deliveries")).json()
    assert [item["id"] for item in inventory] == [delivery["id"]]
    assert inventory[0]["connector"]["connector_type"] == "sink"
    assert inventory[0]["destination"]["id"] == target["id"]
    assert inventory[0]["connect_cluster"]["name"] == "connect"

    detail = (await client.get(f"/api/v1/deliveries/{delivery['id']}")).json()
    assert detail["pipeline_id"] == capture["pipeline_id"]
    assert detail["topics"] == ["capture.public.customers"]
    async with db_factory() as db:
        connector = await db.scalar(select(Connector).where(Connector.connector_type == "sink"))
        assert connector.config_json["consumer.override.group.id"] == connector.name


async def test_destination_crud_encrypts_rotates_and_redacts(client, db_factory, target_payload):
    target = await make_target(client, target_payload)
    identifier = target["id"]
    assert target["actual_state"] == "UNKNOWN" and target["deliveries"] == []
    assert target["last_delivery"] is None
    async with db_factory() as db:
        secret = await db.scalar(select(SecretReference))
        assert target_payload["password"] not in secret.ciphertext
        assert (await EncryptedDatabaseSecretProvider(db).get_secret(secret.id))[
            "password"
        ] == target_payload["password"]
        previous_ref = secret.id
    changed = {**target_payload, "password": "rotated-secret", "description": "Warehouse"}
    assert (await client.put(f"/api/v1/destinations/{identifier}", json=changed)).status_code == 200
    async with db_factory() as db:
        assert (await db.scalar(select(Connection))).secret_ref == previous_ref
        assert (await EncryptedDatabaseSecretProvider(db).get_secret(previous_ref))[
            "password"
        ] == "rotated-secret"
    for path in ["/api/v1/destinations", f"/api/v1/destinations/{identifier}", "/api/v1/audit"]:
        response = await client.get(path)
        assert (
            "rotated-secret" not in response.text
            and target_payload["password"] not in response.text
        )
    assert (await client.delete(f"/api/v1/destinations/{identifier}")).status_code == 200
    async with db_factory() as db:
        assert (await db.scalars(select(SecretReference))).all() == []


async def test_unsaved_connection_does_not_persist_and_validates_password(
    client, target_payload, monkeypatch
):
    async def connected(self):
        return {"status": "HEALTHY"}

    monkeypatch.setattr(PostgresDestinationAdapter, "test_connection", connected)
    assert (
        await client.post("/api/v1/destinations/test-connection", json=target_payload)
    ).status_code == 200
    assert (await client.get("/api/v1/destinations")).json() == []
    assert (
        await client.post("/api/v1/destinations", json={**target_payload, "password": ""})
    ).status_code == 422
    mysql = await client.post(
        "/api/v1/destinations", json={**target_payload, "name": "MySQL target", "type": "mysql"}
    )
    assert mysql.status_code == 201
    assert mysql.json()["type"] == "mysql"


async def test_failed_connection_records_safe_health_and_deduplicated_incident(
    client, target_payload, monkeypatch
):
    target = await make_target(client, target_payload)

    async def failed(self):
        raise DomainError(
            "DESTINATION_AUTH_FAILED", "Destination rejected the supplied credentials", 422
        )

    monkeypatch.setattr(PostgresDestinationAdapter, "test_connection", failed)
    for _ in range(2):
        assert (await client.post(f"/api/v1/destinations/{target['id']}/test")).status_code == 422
    detail = (await client.get(f"/api/v1/destinations/{target['id']}")).json()
    assert detail["status"] == "UNHEALTHY" and detail["last_health_check_at"]
    errors = (await client.get("/api/v1/operations/errors")).json()
    assert len(errors) == 1 and errors[0]["destination_id"] == target["id"]
    assert target_payload["password"] not in json.dumps(errors)


async def test_missing_sink_plugin_is_actionable_and_never_deploys(
    client, capture, target_payload, respx_mock
):
    target = await make_target(client, target_payload)
    respx_mock.get("http://connect:8083/connector-plugins").mock(
        return_value=httpx.Response(200, json=[])
    )
    response = await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=capture)
    assert response.status_code == 422 and response.json()["error"]["code"] == "SINK_PLUGIN_MISSING"
    assert (await client.get(f"/api/v1/destinations/{target['id']}")).json()["deliveries"] == []


@pytest.mark.parametrize(
    "case,expected",
    [
        ("missing", "TOPIC_NOT_FOUND"),
        ("foreign", "TOPIC_NOT_IN_PIPELINE"),
        ("unsupported", "DELIVERY_TYPE_UNSUPPORTED"),
    ],
)
async def test_invalid_delivery_source_rejected(
    client, db_factory, capture, target_payload, monkeypatch, case, expected
):
    target = await make_target(client, target_payload)
    if case == "missing":

        async def empty(self, cluster, name=None):
            return []

        monkeypatch.setattr("app.adapters.kafka.KafkaExplorer.topics", empty)
    elif case == "foreign":
        capture = {
            **capture,
            "mappings": [{"topic": "capture.public.foreign", "table_name": "foreign"}],
        }
    else:
        async with db_factory() as db:
            table = await db.scalar(select(SourceTable))
            table.columns_json = [{"name": "labels", "type": "text[]", "nullable": True}]
            await db.commit()
    response = await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=capture)
    assert response.json()["error"]["code"] == expected
    assert (await client.get(f"/api/v1/destinations/{target['id']}")).json()["deliveries"] == []


async def test_sink_validation_and_connect_failure_leave_no_delivery(
    client, capture, target_payload, respx_mock
):
    target = await make_target(client, target_payload)
    validation = respx_mock.put(
        f"http://connect:8083/connector-plugins/{JDBC_CLASS}/config/validate"
    )
    validation.mock(return_value=httpx.Response(200, json={"error_count": 1, "configs": []}))
    assert (
        await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=capture)
    ).status_code == 422
    validation.mock(return_value=httpx.Response(200, json={"error_count": 0}))
    respx_mock.post("http://connect:8083/connectors").mock(
        side_effect=httpx.ConnectError("sensitive upstream body")
    )
    response = await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=capture)
    assert response.status_code == 503 and "sensitive" not in response.text
    assert (await client.get(f"/api/v1/destinations/{target['id']}")).json()["deliveries"] == []


async def test_fanout_runtime_lifecycle_errors_and_mapping_updates(
    client, db_factory, capture, target_payload, respx_mock
):
    targets = [
        await make_target(client, {**target_payload, "name": name})
        for name in ["Analytics", "Reporting"]
    ]
    links = []
    for target in targets:
        preview = await client.post(f"/api/v1/destinations/{target['id']}/preview", json=capture)
        assert preview.json()["config"]["connection.password"] == "[REDACTED]"
        deployed = await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=capture)
        assert deployed.status_code == 201
        links.append(deployed.json())
    assert (
        len((await client.get(f"/api/v1/pipelines/{capture['pipeline_id']}/destinations")).json())
        == 2
    )
    assert (await client.delete(f"/api/v1/pipelines/{capture['pipeline_id']}")).status_code == 409
    target, link = targets[0], links[0]
    connector_name = link["connector"]["name"]
    status = respx_mock.get(f"http://connect:8083/connectors/{connector_name}/status")
    status.mock(
        return_value=httpx.Response(
            200,
            json={
                "connector": {"state": "RUNNING", "worker_id": "worker"},
                "tasks": [{"id": 0, "state": "FAILED", "trace": target_payload["password"]}],
            },
        )
    )
    for _ in range(2):
        response = await client.get(f"/api/v1/destinations/{target['id']}/status")
        assert (
            response.json()["actual_state"] == "DEGRADED"
            and response.json()["desired_state"] == "RUNNING"
        )
        assert target_payload["password"] not in response.text
    async with db_factory() as db:
        audits = (
            await db.scalars(
                select(AuditLog).where(AuditLog.action == "destination.state_observed")
            )
        ).all()
        assert len(audits) == 1
    errors = (await client.get("/api/v1/operations/errors")).json()
    assert len(errors) == 1 and errors[0]["connector_id"] == link["connector_id"]
    assert (
        await client.patch(f"/api/v1/operations/errors/{errors[0]['id']}?status=ACKNOWLEDGED")
    ).status_code == 200
    respx_mock.put(f"http://connect:8083/connectors/{connector_name}/pause").mock(
        return_value=httpx.Response(202)
    )
    assert (await client.post(f"/api/v1/destinations/{target['id']}/pause")).json()["deliveries"][
        0
    ]["desired_state"] == "PAUSED"
    status.mock(
        return_value=httpx.Response(200, json={"connector": {"state": "PAUSED"}, "tasks": []})
    )
    assert (await client.get(f"/api/v1/destinations/{target['id']}/status")).json()[
        "actual_state"
    ] == "PAUSED"
    respx_mock.put(f"http://connect:8083/connectors/{connector_name}/resume").mock(
        return_value=httpx.Response(202)
    )
    assert (await client.post(f"/api/v1/destinations/{target['id']}/resume")).status_code == 200
    status.mock(
        return_value=httpx.Response(
            200, json={"connector": {"state": "RUNNING"}, "tasks": [{"id": 0, "state": "RUNNING"}]}
        )
    )
    await client.get(f"/api/v1/destinations/{target['id']}/status")
    assert (await client.get("/api/v1/operations/errors")).json()[0]["status"] == "RESOLVED"
    changed = {**capture, "mappings": [{**capture["mappings"][0], "schema_name": "analytics"}]}
    respx_mock.put(f"http://connect:8083/connectors/{connector_name}/config").mock(
        return_value=httpx.Response(200, json={})
    )
    assert (
        await client.put(
            f"/api/v1/destinations/{target['id']}/deliveries/{link['id']}/mappings", json=changed
        )
    ).status_code == 200
    assert (await client.delete(f"/api/v1/destinations/{target['id']}")).status_code == 409
    assert (
        await client.put(
            f"/api/v1/destinations/{target['id']}", json={**target_payload, "host": "changed"}
        )
    ).status_code == 409
    assert (
        await client.put(
            f"/api/v1/destinations/{target['id']}",
            json={**target_payload, "password": None, "description": "new description"},
        )
    ).status_code == 200
    respx_mock.delete(f"http://connect:8083/connectors/{connector_name}").mock(
        return_value=httpx.Response(204)
    )
    assert (
        await client.delete(f"/api/v1/destinations/{target['id']}/deliveries/{link['id']}")
    ).status_code == 200
    assert (await client.delete(f"/api/v1/destinations/{target['id']}")).status_code == 200
    audit = (await client.get("/api/v1/audit?meaningful=true")).text
    assert "destination.mapping_updated" in audit and "destination.state_observed" not in audit
    overview = (await client.get("/api/v1/monitoring/overview")).json()
    assert overview["destinations"] == 1 and overview["throughput"] is None


async def test_destination_viewer_boundary(client, target_payload, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "token")
    token = "destination-viewer"
    monkeypatch.setattr(
        settings,
        "auth_tokens_json",
        json.dumps(
            {hashlib.sha256(token.encode()).hexdigest(): {"actor": "viewer", "role": "Viewer"}}
        ),
    )
    headers = {"Authorization": "Bearer " + token}
    assert (await client.get("/api/v1/destinations", headers=headers)).status_code == 403
    assert (
        await client.post("/api/v1/destinations", json=target_payload, headers=headers)
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/destinations/test-connection", json=target_payload, headers=headers
        )
    ).status_code == 403


def test_supported_types_do_not_allow_sql_fragments():
    assert supported_type("numeric(12,2)") and supported_type("timestamp with time zone")
    assert not supported_type("text; DROP TABLE customers") and not supported_type("text[]")


async def test_sink_metadata_failure_compensates_runtime_creation(
    client, db_factory, capture, target_payload, respx_mock, monkeypatch
):
    target = await make_target(client, target_payload)
    deleted = respx_mock.delete(re.compile(r"http://connect:8083/connectors/cluecdc-delivery-.*"))
    deleted.mock(return_value=httpx.Response(204))
    from app.services.destinations import service

    original = service.audit

    def fail_metadata(session, actor, action, resource, *args):
        if action == "delivery.created":
            raise DomainError("METADATA_UNAVAILABLE", "Delivery metadata could not be saved", 503)
        return original(session, actor, action, resource, *args)

    monkeypatch.setattr(service, "audit", fail_metadata)
    result = await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=capture)
    assert result.status_code == 503 and deleted.call_count == 1
    async with db_factory() as db:
        assert not (await db.scalars(select(PipelineDestination))).all()
        assert not (
            await db.scalars(select(Connector).where(Connector.connector_type == "sink"))
        ).all()
    assert (await client.get(f"/api/v1/destinations/{target['id']}")).json()["deliveries"] == []


async def test_existing_delivery_target_table_cannot_be_overwritten(
    client, capture, target_payload
):
    target = await make_target(client, target_payload)
    assert (
        await client.post(f"/api/v1/destinations/{target['id']}/deploy", json=capture)
    ).status_code == 201
    response = await client.post(
        f"/api/v1/destinations/{target['id']}/preview", json={**capture, "name": "Another delivery"}
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "TARGET_TABLE_IN_USE"


async def test_identical_sink_errors_keep_independent_connector_associations(
    client, capture, target_payload, respx_mock
):
    target = await make_target(client, target_payload)
    for name, table in [("First", "customers"), ("Second", "customers_copy")]:
        response = await client.post(
            f"/api/v1/destinations/{target['id']}/deploy",
            json={
                **capture,
                "name": name,
                "mappings": [{**capture["mappings"][0], "table_name": table}],
            },
        )
        assert response.status_code == 201
    respx_mock.get(re.compile(r"http://connect:8083/connectors/cluecdc-delivery-.*/status")).mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "sink",
                "connector": {"state": "RUNNING"},
                "tasks": [
                    {
                        "id": 0,
                        "state": "FAILED",
                        "trace": "duplicate key private-destination-password",
                    }
                ],
            },
        )
    )
    for _ in range(2):
        response = await client.get(f"/api/v1/destinations/{target['id']}/status")
        assert response.json()["actual_state"] == "DEGRADED"
        assert target_payload["password"] not in response.text
    incidents = (await client.get("/api/v1/operations/errors")).json()
    assert len(incidents) == 2 and len({row["connector_id"] for row in incidents}) == 2
    assert all(
        row["message"] == "Destination constraint violation prevented delivery" for row in incidents
    )
