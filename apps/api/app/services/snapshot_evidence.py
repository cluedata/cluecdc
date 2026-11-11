"""Operational notification ordering; never read row-change payloads."""

import hashlib
import json
from datetime import UTC, datetime

from app.models.entities import now
from app.services.observability_analysis import utc


def accept_notification(table, record, generation="initial"):
    state = dict(table.snapshot_observation_json or {})
    if state.get("generation") != generation:
        state = {"generation": generation}
    if table.snapshot_status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return None
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
    if digest in state.get("seen", []):
        return None
    timestamp = record.get("timestamp")
    try:
        observed = datetime.fromtimestamp(float(timestamp) / 1000, UTC) if timestamp else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    if observed:
        if observed > now() or (
            table.snapshot_started_at and observed < utc(table.snapshot_started_at)
        ):
            return None
        if state.get("observed_at") and observed < datetime.fromisoformat(state["observed_at"]):
            return None
    # Timestamp-less messages can change lifecycle once, but cannot prove fresh source progress.
    accepted_at = observed or now()
    state.update(
        seen=(state.get("seen", []) + [digest])[-64:],
        observed_at=accepted_at.isoformat(),
        provenance="debezium_notification",
        timestamp_verified=observed is not None,
        event=record.get("type"),
    )
    table.snapshot_observation_json = state
    return accepted_at
