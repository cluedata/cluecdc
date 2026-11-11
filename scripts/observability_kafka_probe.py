"""Probe real broker offset/coverage/rebalance/retention behavior on a new owned topic.

Test consumers establish their own commits by consuming records. No existing
group offsets are reset. DeleteRecords on the new probe topic models retained-log
prefix removal; it does not modify consumer commits or any CDC capture topic.
"""

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from confluent_kafka import Consumer, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from cdc_reliability_support import ROOT
from observability_fixture import attach


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    stack = attach(args.fixture)
    bootstrap = stack.url("kafka", 9092).removeprefix("http://")
    suffix = uuid4().hex
    topic, group, connector = (
        "obs_probe_" + suffix,
        "obs_group_" + suffix,
        "obs-" + suffix,
    )
    admin = AdminClient({"bootstrap.servers": bootstrap})
    admin.create_topics([NewTopic(topic, num_partitions=2, replication_factor=1)])[
        topic
    ].result(10)
    sys.path.insert(0, str(ROOT / "apps/api"))
    from app.core import config

    settings = config.Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        connect_secret_token="observability-fixture-token-only-0001",
        secret_encryption_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    )
    config.get_settings = lambda: settings
    from app.models.entities import KafkaCluster
    from app.schemas.observability import DeliveryObservation
    from app.services.observability_providers import metric, sample_backlog

    cluster = KafkaCluster(
        name="owned-probe", bootstrap_servers=bootstrap, security_protocol="PLAINTEXT"
    )
    observations = []

    def probe(name, tasks):
        d = DeliveryObservation(
            delivery_id=uuid4(),
            name="probe",
            expected_running=True,
            backlog=metric("records", "kafka_admin", "NOT_COLLECTED"),
            destination_progress=metric("batches", "destination", "NO_MARKER"),
        )
        try:
            sample_backlog(
                cluster,
                {},
                {"topics": topic, "consumer.override.group.id": group},
                connector,
                d,
                [{"id": i, "state": "RUNNING"} for i in tasks],
            )
        except Exception:
            assert name == "missing_group"
        observations.append(
            {"scenario": name, "observation": d.model_dump(mode="json")}
        )
        return d

    assert probe("missing_group", [0]).backlog.value is None
    producer = Producer({"bootstrap.servers": bootstrap})
    for i in range(3):
        producer.produce(topic, b"probe", partition=0)
    assert producer.flush(10) == 0
    common = {
        "bootstrap.servers": bootstrap,
        "group.id": group,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "partition.assignment.strategy": "range",
    }
    first = Consumer({**common, "client.id": f"connector-consumer-{connector}-0"})
    second = Consumer({**common, "client.id": f"connector-consumer-{connector}-1"})
    stopping, revoked, release = threading.Event(), threading.Event(), threading.Event()
    threads = []
    consumers = [first, second]

    def on_revoke(consumer, partitions):
        revoked.set()
        release.wait(10)

    try:
        first.subscribe([topic], on_revoke=on_revoke)
        consumed = []
        deadline = time.monotonic() + 30
        while len(consumed) < 3 and time.monotonic() < deadline:
            message = first.poll(0.2)
            if message and not message.error():
                consumed.append(message)
        assert len(consumed) == 3
        assert probe("uncommitted_partitions", [0]).backlog.value is None
        first.commit(offsets=[TopicPartition(topic, 0, 3)], asynchronous=False)
        partial = probe("partial_coverage", [0])
        assert partial.required_partitions == 2 and partial.valid_partitions == 1
        assert partial.backlog.value is None
        producer.produce(topic, b"probe", partition=1)
        assert producer.flush(10) == 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = first.poll(0.2)
            if message and not message.error() and message.partition() == 1:
                first.commit(
                    offsets=[TopicPartition(topic, 1, message.offset() + 1)],
                    asynchronous=False,
                )
                break
        complete = probe("complete_coverage", [0])
        assert complete.backlog.status == "AVAILABLE" and complete.backlog.value == 0
        missing_topic = "obs_absent_" + suffix
        absent = DeliveryObservation(
            delivery_id=uuid4(),
            name="missing-topic",
            expected_running=True,
            backlog=metric("records", "kafka_admin", "NOT_COLLECTED"),
            destination_progress=metric("batches", "destination", "NO_MARKER"),
        )
        sample_backlog(
            cluster,
            {},
            {
                "topics": topic + "," + missing_topic,
                "consumer.override.group.id": group,
            },
            connector,
            absent,
            [{"id": 0, "state": "RUNNING"}],
        )
        assert absent.backlog.value is None
        assert missing_topic not in admin.list_topics(timeout=5).topics
        observations.append(
            {
                "scenario": "missing_topic_not_auto_created",
                "observation": absent.model_dump(mode="json"),
            }
        )
        first.pause(first.assignment())
        for i in range(2):
            producer.produce(topic, b"probe", partition=0)
        assert producer.flush(10) == 0
        assert probe("positive_backlog", [0]).backlog.value == 2

        def poll(consumer):
            while not stopping.is_set():
                consumer.poll(0.1)

        for consumer in [first, second]:
            if consumer is second:
                consumer.subscribe([topic])
            thread = threading.Thread(target=poll, args=(consumer,), daemon=True)
            thread.start()
            threads.append(thread)
        assert revoked.wait(20), "Real broker rebalance was not observed"
        rebalance = probe("rebalance", [0, 1])
        assert rebalance.backlog.value is None
        release.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            stable = probe("two_members", [0, 1])
            if stable.backlog.status == "AVAILABLE":
                break
            time.sleep(0.5)
        assert stable.backlog.status == "AVAILABLE" and stable.backlog.value == 2
        assert stable.group_generation != complete.group_generation
        # Exact newly created topic identity is retained in this process, never supplied externally.
        assert topic == "obs_probe_" + suffix
        for future in admin.delete_records([TopicPartition(topic, 0, 5)]).values():
            future.result(10)
        retained = probe("retained_log_prefix_removed", [0, 1])
        assert retained.backlog.value is None
        assert any(
            p.backlog.reason_code == "OFFSET_OUT_OF_RANGE" for p in retained.partitions
        )
        stopping.set()
        release.set()
        for thread in threads:
            thread.join(5)
        threads.clear()
        for consumer in consumers:
            consumer.close()
        consumers.clear()
        # Native consumer recovery from a retained-away commit. Telemetry never
        # issues an offset reset/alter; test commits result from consumed records.
        restarted = Consumer(
            {**common, "client.id": f"connector-consumer-{connector}-0"}
        )
        consumers.append(restarted)
        restarted.subscribe([topic])
        for i in range(2):
            producer.produce(topic, b"after-retention", partition=0)
        assert producer.flush(10) == 0
        messages = []
        deadline = time.monotonic() + 30
        while len(messages) < 2 and time.monotonic() < deadline:
            message = restarted.poll(0.2)
            if message and not message.error() and message.partition() == 0:
                assert message.offset() >= 5
                messages.append(message)
                restarted.commit(message=message, asynchronous=False)
        assert len(messages) == 2
        recovered = probe("native_consumer_reset_after_retention", [0])
        assert recovered.backlog.status == "AVAILABLE" and recovered.backlog.value == 0
        assert recovered.group_generation != stable.group_generation
        (args.fixture.parent / "kafka-probe.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "topic": topic,
                    "group": group,
                    "no_admin_offset_reset": True,
                    "observations": observations,
                },
                indent=2,
            )
        )
        print(
            "Real missing offsets, partial coverage, backlog, rebalance and retained-prefix checks passed"
        )
    finally:
        stopping.set()
        release.set()
        for thread in threads:
            thread.join(5)
        for consumer in consumers:
            consumer.close()


if __name__ == "__main__":
    main()
