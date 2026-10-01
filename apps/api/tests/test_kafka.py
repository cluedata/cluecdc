import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiokafka import TopicPartition
from confluent_kafka import TopicPartition as ConfluentTopicPartition

from app.adapters.kafka import KafkaExplorer
from app.core.errors import DomainError
from app.models.entities import KafkaCluster


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
    assert result["scanned"] == 1000
    assert len(result["events"]) == 50
    assert min(e["offset"] for e in result["events"]) >= 4000
    assert result["events"][0]["changed_fields"] == ["name"]
    assert consumer.options["group_id"] is None
    assert consumer.options["enable_auto_commit"] is False
    consumer.stop.assert_awaited_once()


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


async def test_unknown_topic_does_not_get_created_or_masked_by_cleanup(monkeypatch):
    consumer = Consumer()
    consumer.stop = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaConsumer", lambda **kw: consumer)
    with pytest.raises(DomainError) as exc:
        await KafkaExplorer().events(cluster(), "unknown")
    assert exc.value.code == "TOPIC_NOT_FOUND"
    assert consumer.assigned == []


async def test_invalid_partition_rejected(monkeypatch):
    monkeypatch.setattr("app.adapters.kafka.AIOKafkaConsumer", Consumer)
    with pytest.raises(DomainError) as exc:
        await KafkaExplorer().events(cluster(), "commerce.public.customers", partition=10)
    assert exc.value.code == "PARTITION_NOT_FOUND"


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
