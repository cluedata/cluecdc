from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.adapters.kafka import KafkaExplorer
from app.core.errors import DomainError
from app.models.entities import KafkaCluster
from tests.test_api import setup_pipeline


@pytest.mark.parametrize("code", [0, 36])
async def test_preparation_preserves_existing_topics_and_handles_creation_race(monkeypatch, code):
    admin = SimpleNamespace(
        start=AsyncMock(),
        close=AsyncMock(),
        list_topics=AsyncMock(return_value=["existing", "unrelated"]),
        create_topics=AsyncMock(return_value=SimpleNamespace(topic_errors=[("missing", code, "")])),
    )
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaAdminClient", lambda **kwargs: admin)
    cluster = KafkaCluster(
        name="kafka",
        bootstrap_servers="kafka:9092",
        security_protocol="PLAINTEXT",
    )
    result = await KafkaExplorer().ensure_topics(cluster, ["missing", "existing", "missing"])
    topics = admin.create_topics.await_args.args[0]
    assert [topic.name for topic in topics] == ["missing"]
    assert topics[0].num_partitions == topics[0].replication_factor == -1
    assert topics[0].topic_configs == {}
    assert result == {
        "topics": ["existing", "missing"],
        "created": ["missing"] if code == 0 else [],
    }
    admin.list_topics.return_value = ["existing", "missing"]
    admin.create_topics.reset_mock()
    assert (await KafkaExplorer().ensure_topics(cluster, ["existing", "missing"]))["created"] == []
    admin.create_topics.assert_not_awaited()
    assert admin.close.await_count == 2


async def test_preparation_reports_broker_rejection_without_exposing_response(monkeypatch):
    admin = SimpleNamespace(
        start=AsyncMock(),
        close=AsyncMock(),
        list_topics=AsyncMock(return_value=[]),
        create_topics=AsyncMock(
            return_value=SimpleNamespace(
                topic_errors=[("capture.public.customers", 29, "private broker details")],
            )
        ),
    )
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaAdminClient", lambda **kwargs: admin)
    cluster = KafkaCluster(
        name="kafka",
        bootstrap_servers="kafka:9092",
        security_protocol="PLAINTEXT",
    )
    with pytest.raises(DomainError) as error:
        await KafkaExplorer().ensure_topics(cluster, ["capture.public.customers"])
    assert error.value.code == "KAFKA_TOPIC_CREATION_FAILED"
    assert error.value.details["kafka_error_code"] == 29
    assert "private" not in error.value.message
    admin.close.assert_awaited_once()


async def test_recovery_prepares_only_persisted_capture_topics(
    client,
    db_factory,
    source_payload,
    respx_mock,
    monkeypatch,
):
    data = await setup_pipeline(client, db_factory, source_payload)
    respx_mock.put(
        "http://connect:8083/connector-plugins/io.debezium.connector.postgresql.PostgresConnector/config/validate"
    ).mock(return_value=httpx.Response(200, json={"error_count": 0}))
    pipeline = (await client.post("/api/v1/pipelines", json=data)).json()
    ensure = AsyncMock(return_value={"topics": ["capture.public.customers"], "created": []})
    monkeypatch.setattr("app.adapters.kafka.KafkaExplorer.ensure_topics", ensure)
    response = await client.post(
        f"/api/v1/pipelines/{pipeline['id']}/prepare-topics",
        json={"topics": ["foreign"]},
    )
    assert response.status_code == 200
    assert ensure.await_args.args[1] == ["capture.public.customers"]
    assert "pipeline.topics_prepared" in (await client.get("/api/v1/audit")).text
    assert (await client.post(f"/api/v1/pipelines/{uuid4()}/prepare-topics")).status_code == 404


async def test_topic_preparation_failure_prevents_connector_deployment(
    client,
    db_factory,
    source_payload,
    respx_mock,
    monkeypatch,
):
    data = await setup_pipeline(client, db_factory, source_payload)
    respx_mock.put(
        "http://connect:8083/connector-plugins/io.debezium.connector.postgresql.PostgresConnector/config/validate"
    ).mock(return_value=httpx.Response(200, json={"error_count": 0}))
    pipeline = (await client.post("/api/v1/pipelines", json=data)).json()
    create = respx_mock.post("http://connect:8083/connectors").mock(
        return_value=httpx.Response(201, json={"name": "cluecdc-capture"}),
    )
    monkeypatch.setattr(
        "app.adapters.kafka.KafkaExplorer.ensure_topics",
        AsyncMock(
            side_effect=DomainError(
                "KAFKA_TOPIC_CREATION_FAILED", "Kafka denied topic creation", 503
            ),
        ),
    )
    response = await client.post(f"/api/v1/pipelines/{pipeline['id']}/deploy")
    assert response.status_code == 503
    assert not create.called
    assert (await client.get(f"/api/v1/pipelines/{pipeline['id']}")).json()["connector_id"] is None
