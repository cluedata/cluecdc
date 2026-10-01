from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiokafka.errors import KafkaConnectionError
from sqlalchemy import select

from app.adapters.kafka import KafkaExplorer
from app.core.auth import PERMISSIONS
from app.core.errors import DomainError
from app.models.entities import AuditLog, KafkaCluster


def cluster(name: str = "Local Kafka") -> KafkaCluster:
    return KafkaCluster(
        name=name,
        bootstrap_servers="kafka:29092",
        security_protocol="PLAINTEXT",
    )


class Admin:
    def __init__(self, topic: str, error_code: int = 0, internal: bool = False):
        self.topic = topic
        self.error_code = error_code
        self.internal = internal
        self.start = AsyncMock()
        self.close = AsyncMock()
        self.delete_topics = AsyncMock(
            return_value=SimpleNamespace(topic_error_codes=[(topic, error_code)])
        )

    async def list_topics(self):
        return {self.topic}

    async def describe_topics(self, names):
        return [
            {
                "topic": self.topic,
                "error_code": 0,
                "is_internal": self.internal,
                "partitions": [],
            }
        ]


async def test_delete_topic_preserves_exact_name(monkeypatch):
    name = "commerce.orders.v2-test_1"
    admin = Admin(name)
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaAdminClient", lambda **kwargs: admin)

    result = await KafkaExplorer().delete_topic(cluster(), name)

    assert result == {"deleted": True, "topic": name}
    admin.delete_topics.assert_awaited_once_with([name], timeout_ms=10000)
    admin.close.assert_awaited_once()


@pytest.mark.parametrize(
    ("error_code", "domain_code", "status"),
    [
        (3, "TOPIC_NOT_FOUND", 404),
        (73, "TOPIC_DELETION_DISABLED", 409),
        (29, "KAFKA_AUTHORIZATION_FAILED", 403),
        (31, "KAFKA_AUTHORIZATION_FAILED", 403),
        (7, "KAFKA_TIMEOUT", 504),
        (42, "INTERNAL_ERROR", 500),
    ],
)
async def test_delete_topic_normalizes_broker_errors(monkeypatch, error_code, domain_code, status):
    admin = Admin("orders", error_code=error_code)
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaAdminClient", lambda **kwargs: admin)

    with pytest.raises(DomainError) as exc:
        await KafkaExplorer().delete_topic(cluster(), "orders")

    assert (exc.value.code, exc.value.status) == (domain_code, status)


@pytest.mark.parametrize(
    "name",
    ["__consumer_offsets", "_cluecdc_connect_configs", "connect-offsets"],
)
async def test_delete_topic_rejects_protected_internal_topics(monkeypatch, name):
    admin = Admin(name, internal=name == "__consumer_offsets")
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaAdminClient", lambda **kwargs: admin)

    with pytest.raises(DomainError) as exc:
        await KafkaExplorer().delete_topic(cluster(), name)

    assert (exc.value.code, exc.value.status) == ("PROTECTED_INTERNAL_TOPIC", 409)
    admin.delete_topics.assert_not_awaited()


async def test_delete_topic_checks_existence_before_delete(monkeypatch):
    admin = Admin("another-topic")
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaAdminClient", lambda **kwargs: admin)

    with pytest.raises(DomainError) as exc:
        await KafkaExplorer().delete_topic(cluster(), "missing")

    assert (exc.value.code, exc.value.status) == ("TOPIC_NOT_FOUND", 404)
    admin.delete_topics.assert_not_awaited()


async def test_delete_topic_normalizes_unavailable_broker(monkeypatch):
    admin = Admin("orders")
    admin.list_topics = AsyncMock(side_effect=KafkaConnectionError("broker unavailable"))
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaAdminClient", lambda **kwargs: admin)

    with pytest.raises(DomainError) as exc:
        await KafkaExplorer().delete_topic(cluster(), "orders")

    assert (exc.value.code, exc.value.status) == ("KAFKA_UNAVAILABLE", 503)


async def test_delete_api_audits_success_and_uses_registered_cluster(
    client, db_factory, monkeypatch
):
    kafka = cluster()
    async with db_factory() as db:
        db.add(kafka)
        await db.commit()

    deleted = AsyncMock(return_value={"deleted": True, "topic": "orders.v2-test"})
    monkeypatch.setattr(KafkaExplorer, "delete_topic", deleted)

    response = await client.delete(f"/api/v1/kafka/topics/orders.v2-test?cluster_id={kafka.id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "topic": "orders.v2-test"}
    assert deleted.await_args.args[0].id == kafka.id
    assert deleted.await_args.args[1] == "orders.v2-test"
    async with db_factory() as db:
        entry = await db.scalar(select(AuditLog).where(AuditLog.action == "kafka.topic.deleted"))
        assert entry is not None
        assert entry.actor == "local-developer"
        assert entry.resource_id == "orders.v2-test"
        assert entry.after_json == {"result": "succeeded"}


async def test_delete_api_audits_normalized_failure(client, db_factory, monkeypatch):
    kafka = cluster()
    async with db_factory() as db:
        db.add(kafka)
        await db.commit()

    monkeypatch.setattr(
        KafkaExplorer,
        "delete_topic",
        AsyncMock(
            side_effect=DomainError("TOPIC_DELETION_DISABLED", "Topic deletion is disabled", 409)
        ),
    )
    response = await client.delete(f"/api/v1/kafka/topics/orders?cluster_id={kafka.id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TOPIC_DELETION_DISABLED"
    async with db_factory() as db:
        entry = await db.scalar(
            select(AuditLog).where(AuditLog.action == "kafka.topic.delete.failed")
        )
        assert entry is not None
        assert entry.after_json["error_code"] == "TOPIC_DELETION_DISABLED"


def test_topic_delete_permission_is_admin_only():
    assert "kafka.topic.delete" in PERMISSIONS["PlatformAdmin"]
    assert "kafka.topic.delete" not in PERMISSIONS["DataEngineer"]
    assert "kafka.topic.delete" not in PERMISSIONS["Viewer"]
