"""Test-only direct Kafka verification. Never imports or routes through the API."""

import argparse
import json
import os
import time
from uuid import uuid4

from confluent_kafka import Consumer, TopicPartition


def sample_records(topic: str, bootstrap: str | None = None) -> dict:
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap
            or os.getenv("KAFKA_TEST_BOOTSTRAP", "localhost:9092"),
            "group.id": "cluecdc-test-direct-" + uuid4().hex,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "allow.auto.create.topics": False,
            "broker.address.family": "v4",
        }
    )
    try:
        metadata = consumer.list_topics(topic, timeout=5).topics.get(topic)
        if metadata is None or metadata.error:
            return {"events": [], "scanned": 0}
        partitions = sorted(metadata.partitions)
        if not partitions or len(partitions) > 32:
            raise RuntimeError(
                "Direct verification requires a topic with 1..32 partitions"
            )
        budget = 1000
        ends = {}
        assignment = []
        for partition in partitions:
            low, high = consumer.get_watermark_offsets(
                TopicPartition(topic, partition), timeout=5
            )
            ends[partition] = high
            assignment.append(
                TopicPartition(
                    topic, partition, max(low, high - budget // len(partitions))
                )
            )
        consumer.assign(assignment)
        records = []
        scanned = size = 0
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and scanned < budget and size < 2_000_000:
            message = consumer.poll(0.2)
            if message is None:
                continue
            if message.error():
                raise RuntimeError(
                    "Direct Kafka verification failed; broker detail withheld"
                )
            if message.offset() >= ends[message.partition()]:
                continue
            scanned += 1
            size += len(message.value() or b"") + len(message.key() or b"")
            if not message.value():
                continue
            raw = json.loads(message.value())
            payload = raw.get("payload", raw)
            before, after = payload.get("before"), payload.get("after")
            operation = {"c": "CREATE", "u": "UPDATE", "d": "DELETE", "r": "READ"}.get(
                payload.get("op")
            )
            records.append(
                {
                    "operation": operation,
                    "before": before,
                    "after": after,
                    "key": json.loads(message.key()) if message.key() else None,
                    "changed_fields": sorted(
                        k
                        for k in set(before or {}) | set(after or {})
                        if (before or {}).get(k) != (after or {}).get(k)
                    ),
                }
            )
        return {"events": records, "scanned": scanned}
    finally:
        consumer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--contains", required=True)
    args = parser.parse_args()
    sample = sample_records(args.topic)
    # Browser integration needs only a match boolean, not the record body.
    print(
        json.dumps(
            {
                "matched": any(
                    args.contains in json.dumps(event) for event in sample["events"]
                )
            }
        )
    )
