import json
from typing import Any

from aiokafka.structs import OffsetAndMetadata, TopicPartition

from app.core.errors import DomainError


def decode_json(value: bytes | None) -> Any:
    if value is None:
        return None
    return json.loads(value.decode("utf-8"))


def normalize_event(record) -> dict:
    key = decode_json(record.key)
    value = decode_json(record.value)
    envelope: dict[str, Any]
    operation: str | None
    if value is None:
        envelope = {}
        operation = "tombstone"
    else:
        raw_envelope = value.get("payload", value) if isinstance(value, dict) else {}
        envelope = raw_envelope if isinstance(raw_envelope, dict) else {}
        candidate = envelope.get("op")
        operation = candidate if isinstance(candidate, str) else None
        if operation not in {"c", "r", "u", "d"}:
            raise DomainError(
                "WAREHOUSE_CDC_EVENT_INVALID",
                "Kafka record is not a supported Debezium change envelope",
                422,
            )
    return {
        "topic": record.topic,
        "partition": record.partition,
        "offset": record.offset,
        "timestamp": record.timestamp,
        "operation": operation,
        "key": key,
        "before": envelope.get("before"),
        "after": envelope.get("after"),
        "source": envelope.get("source"),
    }


def commit_map(offsets: dict[str, dict[str, int]]) -> dict:
    result = {}
    for key, bounds in offsets.items():
        topic, partition = key.rsplit(":", 1)
        result[TopicPartition(topic, int(partition))] = OffsetAndMetadata(bounds["last"] + 1, "")
    return result
