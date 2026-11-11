"""Deterministic findings and confirmation counters. Absence never implies recovery."""

from datetime import UTC, datetime

from app.core.config import get_settings
from app.schemas.observability import Finding, Observation


def utc(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("A collected observation must have a timestamp")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def continuity_seconds() -> float:
    settings = get_settings()
    return settings.observability_interval_seconds + settings.observability_budget_seconds + 5


def analyse(
    sample: Observation,
    history: list[Observation],
    *,
    stall_seconds=300,
    snapshot_seconds=900,
    capture_threshold=300000,
    backlog_threshold=10000,
):
    assert sample.collected_at is not None
    max_gap = continuity_seconds()
    findings = []

    def finding(code, explanation, action, evidence, delivery=None, confidence="DIRECT"):
        findings.append(
            Finding(
                code=code,
                severity="info" if code == "NO_BOTTLENECK_EVIDENCE" else "warning",
                pipeline_id=sample.pipeline_id,
                delivery_id=delivery,
                observations=evidence,
                observed_at=sample.collected_at,
                confidence=confidence,
                explanation=explanation,
                suggested_action=action,
            )
        )

    recent = [
        s for s in history if s.collected_at and utc(s.collected_at) < utc(sample.collected_at)
    ] + [sample]
    # Require contiguous samples and stable identities; gaps reset all progress evidence.
    for delivery in sample.deliveries:
        if delivery.backlog.status != "AVAILABLE":
            continue
        if (delivery.backlog.value or 0) > 0:
            finding(
                "SINK_BACKLOG_DETECTED",
                "Verified consumer backlog is positive.",
                "Inspect delivery task rates and destination availability.",
                [
                    f"backlog={delivery.backlog.value}; coverage="
                    f"{delivery.valid_partitions}/{delivery.required_partitions}"
                ],
                delivery.delivery_id,
            )
        consecutive: list[Observation] = []
        identity = None
        offsets = None
        for point in reversed(recent):
            previous = next(
                (d for d in point.deliveries if d.delivery_id == delivery.delivery_id), None
            )
            if (
                not previous
                or not previous.expected_running
                or previous.connector_state != "RUNNING"
                or previous.group_state != "STABLE"
                or previous.backlog.status != "AVAILABLE"
                or not previous.backlog.value
            ):
                break
            current_identity = (
                point.connector_id,
                previous.connector_id,
                previous.group_id,
                previous.group_generation,
                tuple((p.topic, p.partition) for p in previous.partitions),
            )
            current_offsets = tuple(p.committed_offset for p in previous.partitions)
            if identity is not None and (
                current_identity != identity or current_offsets != offsets
            ):
                break
            if (
                consecutive
                and (utc(consecutive[-1].collected_at) - utc(point.collected_at)).total_seconds()
                > max_gap
            ):
                break
            identity, offsets = current_identity, current_offsets
            consecutive.append(point)
        if (
            len(consecutive) >= 3
            and delivery.expected_running
            and (utc(sample.collected_at) - utc(consecutive[-1].collected_at)).total_seconds()
            >= max(300, stall_seconds)
        ):
            finding(
                "SINK_PROGRESS_STALLED",
                "Positive backlog and unchanged committed offsets "
                "persist across at least three consecutive samples for the stall window.",
                "Inspect sink errors, throttling and destination availability; recovery is manual.",
                [
                    f"samples={len(consecutive)}",
                    f"since={utc(consecutive[-1].collected_at).isoformat()}",
                ],
                delivery.delivery_id,
            )
    lag = sample.capture_lag
    if (
        sample.capture_expected_running
        and sample.connector_state == "RUNNING"
        and lag.status == "AVAILABLE"
        and (lag.value or 0) > capture_threshold
        and sample.capture_events.status == "AVAILABLE"
        and len(recent) >= 2
    ):
        before = recent[-2]
        if (
            before.capture_events.status == "AVAILABLE"
            and before.connector_id == sample.connector_id
            and before.capture_events.value is not None
            and sample.capture_events.value is not None
            and sample.capture_events.value > before.capture_events.value
            and (utc(sample.collected_at) - utc(before.collected_at)).total_seconds() <= max_gap
        ):
            finding(
                "CAPTURE_DELAY_SUSPECTED",
                "Capture delay is high while the connector's "
                "captured event counter advances. This is pipeline-scoped evidence.",
                "Inspect source load and capture worker capacity.",
                [f"lag_ms={lag.value}", "captured_event_counter_advanced"],
                confidence="INDIRECT",
            )
    for snapshot in sample.snapshots:
        if (
            snapshot.state != "RUNNING"
            or not sample.capture_expected_running
            or sample.connector_state != "RUNNING"
        ):
            continue
        valid: list[Observation] = []
        for point in reversed(recent):
            prior_snapshot = next(
                (s for s in point.snapshots if s.table_id == snapshot.table_id), None
            )
            if (
                not prior_snapshot
                or prior_snapshot.state != "RUNNING"
                or not prior_snapshot.running
                or not prior_snapshot.paused
                or not prior_snapshot.progress_counter
                or any(
                    m.status != "AVAILABLE"
                    for m in [
                        prior_snapshot.running,
                        prior_snapshot.paused,
                        prior_snapshot.progress_counter,
                    ]
                )
                or prior_snapshot.running.value != 1
                or prior_snapshot.paused.value != 0
                or prior_snapshot.started_at != snapshot.started_at
                or prior_snapshot.progress_counter.value
                != (snapshot.progress_counter.value if snapshot.progress_counter else None)
                or not point.capture_expected_running
                or point.connector_state != "RUNNING"
            ):
                break
            if (
                valid
                and (utc(valid[-1].collected_at) - utc(point.collected_at)).total_seconds()
                > max_gap
            ):
                break
            valid.append(point)
        if (
            len(valid) >= 3
            and (utc(sample.collected_at) - utc(valid[-1].collected_at)).total_seconds()
            >= snapshot_seconds
        ):
            finding(
                "SNAPSHOT_PROGRESS_STALLED",
                "Fresh snapshot metrics report running, "
                "unpaused capture without event-counter progress throughout the configured window.",
                "Inspect snapshot locks, long chunks and source resource limits.",
                [
                    f"table_id={snapshot.table_id}",
                    f"samples={len(valid)}",
                    "scope=pipeline_snapshot_counter",
                ],
                confidence="INDIRECT",
            )
        elif snapshot.stale:
            finding(
                "OBSERVABILITY_INCOMPLETE",
                "Snapshot progress has not been confirmed "
                "recently; a long chunk or expected wait cannot be excluded.",
                "Inspect Debezium notifications and snapshot task logs.",
                [f"table_id={snapshot.table_id}"],
                confidence="UNKNOWN",
            )
    if (
        sample.capture_lag.status != "AVAILABLE"
        or not sample.deliveries
        or any(d.backlog.status != "AVAILABLE" for d in sample.deliveries)
        or any(
            m.status != "AVAILABLE"
            for d in sample.deliveries
            for m in [d.destination_progress, *d.sink_metrics.values()]
        )
    ):
        finding(
            "OBSERVABILITY_INCOMPLETE",
            "One or more stages lack complete measured evidence.",
            "Check trusted exporters, worker coverage and consumer permissions.",
            [f"capture={sample.capture_lag.status}"],
            confidence="UNKNOWN",
        )
    if not findings:
        finding(
            "NO_BOTTLENECK_EVIDENCE",
            "Available measurements show no bottleneck evidence; "
            "this does not establish record-level correctness.",
            "Continue monitoring.",
            ["available_stage_measurements"],
            confidence="INDIRECT",
        )
    return findings


def confirmation(previous: dict, key: str, violation: bool | None, at: datetime):
    state = dict(previous.get(key, {}))
    max_gap = continuity_seconds()
    if state.get("at") and utc(at) <= datetime.fromisoformat(state["at"]):
        return None
    # Unknown breaks consecutive confirmation, but must not resolve an incident.
    if violation is None:
        state.update(firing=0, recovery=0)
    else:
        last = state.get("at")
        if last and (utc(at) - datetime.fromisoformat(last)).total_seconds() > max_gap:
            state.update(firing=0, recovery=0)
        counter, other = ("firing", "recovery") if violation else ("recovery", "firing")
        state[counter] = state.get(counter, 0) + 1
        state[other] = 0
    state["at"] = utc(at).isoformat()
    previous[key] = state
    if state.get("firing", 0) >= 3:
        return "firing"
    if state.get("recovery", 0) >= 2:
        return "resolved"
    return None
