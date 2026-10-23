import json
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from aiokafka import TopicPartition
from confluent_kafka import TopicPartition as ConfluentTopicPartition
from sqlalchemy import select

from app.adapters.kafka import (
    KafkaExplorer,
    admin_options,
    connector_security,
    options,
    source_connector_security,
)
from app.core.errors import DomainError
from app.models.entities import KafkaCluster, SecretReference
from app.schemas.requests import KafkaInput
from app.services.secrets import EncryptedDatabaseSecretProvider


class Consumer:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.pos = 0
        self.assigned = []

    async def topics(self):
        return {"commerce.public.customers"}

    # This deliberately stays synchronous to match aiokafka's public API.
    def partitions_for_topic(self, topic):
        return {0}

    def assign(self, partitions):
        self.assigned = partitions

    async def end_offsets(self, partitions):
        return {p: 5000 for p in partitions}

    async def beginning_offsets(self, partitions):
        return {p: 0 for p in partitions}

    def seek(self, partition, offset):
        self.pos = offset

    async def position(self, partition):
        return self.pos

    async def getmany(self, timeout_ms, max_records):
        count = min(max_records, 5000 - self.pos)
        records = []
        for i in range(count):
            value = {
                "op": "u",
                "before": {"id": self.pos, "name": "old"},
                "after": {"id": self.pos, "name": "new"},
                "source": {"table": "customers"},
            }
            records.append(
                SimpleNamespace(
                    offset=self.pos,
                    partition=0,
                    timestamp=self.pos,
                    key=json.dumps({"id": self.pos}).encode(),
                    value=json.dumps(value).encode(),
                )
            )
            self.pos += 1
        return {TopicPartition("commerce.public.customers", 0): records}


class ConsumerGroupAdmin:
    def __init__(self, options=None):
        self.options = options

    def list_consumer_groups(self, **kwargs):
        return Future(
            SimpleNamespace(valid=[SimpleNamespace(group_id="cluecdc-delivery-1")], errors=[])
        )

    def describe_consumer_groups(self, group_ids, **kwargs):
        assert group_ids == ["cluecdc-delivery-1"]
        return {
            "cluecdc-delivery-1": Future(
                SimpleNamespace(
                    state=SimpleNamespace(name="STABLE"),
                    partition_assignor="range",
                    members=[("member-1",), ("member-2",)],
                )
            )
        }

    def list_consumer_group_offsets(self, request, **kwargs):
        assert request[0].group_id == "cluecdc-delivery-1"
        return {
            "cluecdc-delivery-1": Future(
                SimpleNamespace(
                    topic_partitions=[
                        ConfluentTopicPartition("commerce.public.customers", 0, 90),
                        ConfluentTopicPartition("commerce.public.customers", 1, 55),
                    ]
                )
            )
        }

    def list_offsets(self, request, **kwargs):
        return {
            partition: Future(SimpleNamespace(offset=100 if partition.partition == 0 else 60))
            for partition in request
        }


class Future:
    def __init__(self, value):
        self.value = value

    def result(self, timeout=None):
        return self.value


def cluster():
    return KafkaCluster(bootstrap_servers="kafka:9092", security_protocol="PLAINTEXT")


@pytest.mark.parametrize(
    "protocol,mechanism",
    [
        ("SASL_PLAINTEXT", "PLAIN"),
        ("SASL_SSL", "SCRAM-SHA-256"),
        ("SASL_SSL", "SCRAM-SHA-512"),
    ],
)
def test_kafka_sasl_client_and_connector_options(protocol, mechanism):
    secured = KafkaCluster(
        bootstrap_servers="broker:9093",
        security_protocol=protocol,
        security_config_json={"sasl_mechanism": mechanism},
    )
    secured.secret_ref = UUID("11111111-1111-1111-1111-111111111111")
    credentials = {"username": "kafka-user", "password": "kafka-password"}

    async_options = options(secured, credentials)
    assert async_options["sasl_mechanism"] == mechanism
    assert async_options["sasl_plain_username"] == "kafka-user"
    assert async_options["sasl_plain_password"] == "kafka-password"
    if protocol == "SASL_SSL":
        assert async_options["ssl_context"].check_hostname is True
        assert async_options["ssl_context"].verify_mode.name == "CERT_REQUIRED"

    sync_options = admin_options(secured, credentials)
    assert sync_options["sasl.mechanism"] == mechanism
    assert sync_options["sasl.username"] == "kafka-user"
    assert sync_options["sasl.password"] == "kafka-password"

    generated = connector_security(secured, "consumer.override")
    assert generated["consumer.override.sasl.mechanism"] == mechanism
    assert "kafka-user" not in json.dumps(generated)
    assert "kafka-password" not in json.dumps(generated)
    assert "${cluecdc:" in generated["consumer.override.sasl.jaas.config"]
    source_generated = source_connector_security(secured, mysql=True)
    assert source_generated["producer.override.security.protocol"] == protocol
    assert source_generated["schema.history.internal.producer.sasl.mechanism"] == mechanism
    assert source_generated["schema.history.internal.consumer.sasl.mechanism"] == mechanism


@pytest.mark.parametrize(
    "prefix",
    [
        "producer.override",
        "consumer.override",
        "schema.history.internal.producer",
        "schema.history.internal.consumer",
    ],
)
def test_inline_tls_connector_credentials_use_only_secret_references(prefix):
    """SEC-03: each source/history/sink client gets inline PEM through the secret provider."""
    secured = KafkaCluster(
        bootstrap_servers="broker:9093",
        security_protocol="SSL",
        secret_ref=UUID(int=1),
        security_config_json={"has_custom_ca": True, "has_client_certificate": True},
    )
    overrides = connector_security(secured, prefix)
    assert f"{prefix}.ssl.truststore.location" not in overrides
    assert f"{prefix}.ssl.keystore.location" not in overrides
    assert overrides[f"{prefix}.ssl.truststore.certificates"].startswith("${cluecdc:")
    assert overrides[f"{prefix}.ssl.keystore.key"].startswith("${cluecdc:")


def test_kafka_security_input_rejects_incompatible_credentials():
    with pytest.raises(ValueError, match="supported mechanism"):
        KafkaInput(
            name="secure",
            bootstrap_servers="broker:9093",
            security_protocol="SASL_SSL",
        )
    with pytest.raises(ValueError, match="TLS credentials require"):
        KafkaInput(
            name="plain",
            bootstrap_servers="broker:9092",
            security_protocol="PLAINTEXT",
            ssl_ca_pem="not-allowed",
        )


def test_kafka_tls_rejects_invalid_custom_ca():
    secured = KafkaCluster(
        bootstrap_servers="broker:9093",
        security_protocol="SSL",
        security_config_json={"has_custom_ca": True},
    )
    with pytest.raises(ssl.SSLError):
        options(secured, {"ssl_ca_pem": "not-a-certificate"})
    with pytest.raises(ValueError, match="supplied together"):
        KafkaInput(
            name="tls",
            bootstrap_servers="broker:9093",
            security_protocol="SSL",
            ssl_certificate_pem="certificate-only",
        )


async def test_kafka_api_encrypts_and_masks_sasl_credentials(client, db_factory):
    payload = {
        "name": "secured Kafka",
        "bootstrap_servers": "broker:9093",
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "SCRAM-SHA-512",
        "username": "kafka-user",
        "password": "kafka-password",
    }
    response = await client.post("/api/v1/kafka/clusters", json=payload)
    assert response.status_code == 201
    assert "kafka-user" not in response.text
    assert "kafka-password" not in response.text
    assert response.json()["credentials"]["configured"] is True
    listed = await client.get("/api/v1/kafka/clusters")
    assert "kafka-user" not in listed.text and "kafka-password" not in listed.text

    async with db_factory() as db:
        secret = await db.scalar(select(SecretReference))
        secret_id = secret.id
        values = await EncryptedDatabaseSecretProvider(db).get_secret(secret.id)
        assert values == {"username": "kafka-user", "password": "kafka-password"}
        assert "kafka-user" not in secret.ciphertext
        assert "kafka-password" not in secret.ciphertext

    rotated_payload = {key: value for key, value in payload.items() if key != "username"}
    rotated_payload["password"] = "rotated-kafka-password"
    rotated = await client.put(
        f"/api/v1/kafka/clusters/{response.json()['id']}", json=rotated_payload
    )
    assert rotated.status_code == 200
    assert "rotated-kafka-password" not in rotated.text
    async with db_factory() as db:
        secret = await db.get(SecretReference, secret_id)
        assert await EncryptedDatabaseSecretProvider(db).get_secret(secret.id) == {
            "username": "kafka-user",
            "password": "rotated-kafka-password",
        }


async def test_production_plaintext_kafka_requires_explicit_override(client, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "allow_insecure_kafka", False)
    payload = {
        "name": "plaintext Kafka",
        "bootstrap_servers": "broker:9092",
        "security_protocol": "PLAINTEXT",
    }
    rejected = await client.post("/api/v1/kafka/clusters", json=payload)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "INSECURE_KAFKA_DISABLED"

    monkeypatch.setattr(settings, "allow_insecure_kafka", True)
    assert (await client.post("/api/v1/kafka/clusters", json=payload)).status_code == 201


@pytest.fixture(autouse=True)
def admin_metadata(monkeypatch):
    async def metadata(self, cluster, name=None):
        if name != "commerce.public.customers":
            raise DomainError("TOPIC_NOT_FOUND", "Kafka topic was not found", 404)
        return [{"partition_details": [{"partition": 0}]}]

    monkeypatch.setattr(KafkaExplorer, "topics", metadata)


async def test_bounded_recent_sample_never_commits(monkeypatch):
    consumer = Consumer()

    def factory(**kwargs):
        consumer.options = kwargs
        return consumer

    monkeypatch.setattr("app.adapters.kafka.AIOKafkaConsumer", factory)

    result = await KafkaExplorer().events(cluster(), "commerce.public.customers", limit=50)

    assert result["bounded"] is True
    assert result["scanned"] == 1000
    assert len(result["events"]) == 50
    assert min(event["offset"] for event in result["events"]) >= 4000
    assert result["events"][0]["changed_fields"] == ["name"]
    assert consumer.options["group_id"] is None
    assert consumer.options["enable_auto_commit"] is False
    consumer.stop.assert_awaited_once()


async def test_event_api_rejects_topics_outside_registered_pipelines(client, monkeypatch):
    consumer = Consumer()
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaConsumer", lambda **kwargs: consumer)
    cluster_response = await client.post(
        "/api/v1/kafka/clusters",
        json={"name": "Local Kafka", "bootstrap_servers": "kafka:9092"},
    )

    response = await client.get(
        f"/api/v1/events?cluster_id={cluster_response.json()['id']}&topic=customers"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TOPIC_NOT_INSPECTABLE"
    consumer.start.assert_not_awaited()


async def test_consumer_group_report_uses_committed_and_end_offsets(monkeypatch):
    admin = ConsumerGroupAdmin()
    monkeypatch.setattr("app.adapters.kafka.AdminClient", lambda options: admin)

    groups = await KafkaExplorer().consumer_groups(cluster())

    assert [group["group_id"] for group in groups] == ["cluecdc-delivery-1"]
    assert groups[0]["state"] == "STABLE"
    assert groups[0]["members"] == 2
    assert groups[0]["total_lag"] == 15
    assert groups[0]["offsets"] == [
        {
            "topic": "commerce.public.customers",
            "partition": 0,
            "committed_offset": 90,
            "end_offset": 100,
            "lag": 10,
        },
        {
            "topic": "commerce.public.customers",
            "partition": 1,
            "committed_offset": 55,
            "end_offset": 60,
            "lag": 5,
        },
    ]


async def test_consumer_group_api_reports_registered_clusters(client, monkeypatch):
    cluster_response = await client.post(
        "/api/v1/kafka/clusters",
        json={"name": "Local Kafka", "bootstrap_servers": "kafka:9092"},
    )
    cluster_id = cluster_response.json()["id"]
    monkeypatch.setattr(
        KafkaExplorer,
        "consumer_groups",
        AsyncMock(
            return_value=[
                {
                    "group_id": "delivery-group",
                    "state": "Stable",
                    "protocol_type": "consumer",
                    "protocol": "range",
                    "members": 1,
                    "topics": ["orders"],
                    "total_lag": 2,
                    "offsets": [
                        {
                            "topic": "orders",
                            "partition": 0,
                            "committed_offset": 8,
                            "end_offset": 10,
                            "lag": 2,
                        }
                    ],
                }
            ]
        ),
    )

    response = await client.get("/api/v1/kafka/consumer-groups")

    assert response.status_code == 200
    report = response.json()
    assert report["clusters"] == [
        {
            "id": cluster_id,
            "name": "Local Kafka",
            "status": "HEALTHY",
            "group_count": 1,
            "error": None,
        }
    ]
    assert report["groups"][0]["cluster_id"] == cluster_id
    assert report["groups"][0]["offsets"][0]["lag"] == 2


async def test_notifications_tolerate_an_empty_consumer_warmup_poll(monkeypatch):
    consumer = Consumer()
    consumer.pos = 0
    consumer.end_offsets = AsyncMock(
        side_effect=lambda partitions: {partition: 1 for partition in partitions}
    )
    consumer.beginning_offsets = AsyncMock(
        side_effect=lambda partitions: {partition: 0 for partition in partitions}
    )
    notification = {
        "id": "snapshot-1",
        "type": "TABLE_SCAN_COMPLETED",
        "additional_data": {"status": "SUCCEEDED", "total_rows_scanned": "42"},
    }
    consumer.getmany = AsyncMock(
        side_effect=[
            {},
            {
                TopicPartition("cluecdc.notifications.commerce", 0): [
                    SimpleNamespace(offset=0, value=json.dumps(notification).encode())
                ]
            },
        ]
    )

    async def position(partition):
        return 1 if consumer.getmany.await_count > 1 else 0

    consumer.position = position
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaConsumer", lambda **kwargs: consumer)

    async def metadata(self, cluster, name=None):
        return [{"partition_details": [{"partition": 0}]}]

    monkeypatch.setattr(KafkaExplorer, "topics", metadata)
    records = await KafkaExplorer().notifications(cluster(), "cluecdc.notifications.commerce")

    assert records == [notification]
    assert consumer.getmany.await_count == 2
