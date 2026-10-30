import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.core.errors import DomainError


@dataclass(frozen=True)
class BatchPlan:
    batch_id: str
    source_offsets: dict[str, dict[str, int]]
    record_count: int
    staging_key: str


def plan_batch(delivery_id: str, events: list[dict], staging_prefix: str) -> BatchPlan:
    if not events:
        raise DomainError("AZURE_SYNAPSE_BATCH_EMPTY", "Cannot plan an empty batch", 422)
    offsets: dict[str, dict[str, int]] = {}
    for event in events:
        topic = str(event["topic"])
        partition = int(event["partition"])
        offset = int(event["offset"])
        key = f"{topic}:{partition}"
        current = offsets.setdefault(key, {"first": offset, "last": offset})
        current["first"] = min(current["first"], offset)
        current["last"] = max(current["last"], offset)
    identity = json.dumps(
        {"delivery": delivery_id, "offsets": offsets}, sort_keys=True, separators=(",", ":")
    )
    batch_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
    prefix = staging_prefix.strip("/")
    return BatchPlan(
        batch_id=batch_id,
        source_offsets=offsets,
        record_count=len(events),
        staging_key=f"{prefix}/deliveries/{delivery_id}/batches/{batch_id}/records.csv",
    )


def append_rows(events: list[dict]) -> list[dict]:
    """Preserve event semantics and ordering metadata; no row key is required."""
    return [
        {
            "_clue_topic": event["topic"],
            "_clue_partition": int(event["partition"]),
            "_clue_offset": int(event["offset"]),
            "_clue_timestamp": event.get("timestamp"),
            "_clue_operation": event.get("operation"),
            "_clue_key": event.get("key"),
            "_clue_before": event.get("before"),
            "_clue_after": event.get("after"),
            "_clue_source": event.get("source"),
        }
        for event in sorted(
            events,
            key=lambda item: (
                str(item["topic"]),
                int(item["partition"]),
                int(item["offset"]),
            ),
        )
    ]


def _row_key(row: dict[str, Any] | None, columns: tuple[str, ...]) -> tuple[Any, ...]:
    if row is None or any(column not in row or row[column] is None for column in columns):
        raise DomainError(
            "AZURE_SYNAPSE_EFFECTIVE_KEY_INVALID",
            "A stateful CDC event is missing an Effective Row Key value",
            422,
        )
    return tuple(row[column] for column in columns)


def reduce_stateful(events: list[dict], effective_key: list[str]) -> list[dict]:
    """Collapse a batch per key, including delete+insert key-changing updates."""
    columns = tuple(effective_key)
    if not columns or len(columns) != len(set(columns)):
        raise DomainError(
            "AZURE_SYNAPSE_EFFECTIVE_KEY_INVALID",
            "Stateful CDC requires a valid Effective Row Key",
            422,
        )
    mutations: dict[tuple[Any, ...], dict] = {}
    ordered = sorted(
        events,
        key=lambda item: (
            str(item["topic"]),
            int(item["partition"]),
            int(item["offset"]),
        ),
    )
    for event in ordered:
        operation = str(event.get("operation", "")).lower()
        before = event.get("before")
        after = event.get("after")
        position = {
            "topic": event["topic"],
            "partition": int(event["partition"]),
            "offset": int(event["offset"]),
            "source": event.get("source"),
        }
        if operation == "d":
            key = _row_key(before or event.get("key"), columns)
            mutations[key] = {"action": "DELETE", "key": key, "row": None, **position}
            continue
        if operation not in {"c", "r", "u"} or after is None:
            raise DomainError(
                "AZURE_SYNAPSE_CDC_EVENT_INVALID", "CDC event operation is unsupported", 422
            )
        new_key = _row_key(after, columns)
        if operation == "u" and before is not None:
            old_key = _row_key(before, columns)
            if old_key != new_key:
                mutations[old_key] = {
                    "action": "DELETE",
                    "key": old_key,
                    "row": None,
                    **position,
                }
        mutations[new_key] = {
            "action": "UPSERT",
            "key": new_key,
            "row": after,
            **position,
        }
    return sorted(mutations.values(), key=lambda item: repr(item["key"]))


def classify_error(error: Exception) -> dict[str, Any]:
    text = str(error).lower()
    if "42108" in text or "paused" in text:
        return {"code": "AZURE_SYNAPSE_POOL_PAUSED", "retryable": True}
    if any(code in text for code in ("40501", "10928", "10929", "throttl")):
        return {"code": "AZURE_SYNAPSE_THROTTLED", "retryable": True}
    if any(value in text for value in ("timeout", "temporarily unavailable", "connection reset")):
        return {"code": "AZURE_SYNAPSE_TRANSIENT", "retryable": True}
    if any(value in text for value in ("401", "403", "permission", "login failed")):
        return {"code": "AZURE_SYNAPSE_PERMISSION_DENIED", "retryable": False}
    return {"code": "AZURE_SYNAPSE_APPLY_FAILED", "retryable": False}
