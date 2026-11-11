"""Explicit-rule CDC alerts using durable sample confirmation and existing delivery."""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.alerts.domain import AlertEvent
from app.alerts.manager import AlertManager
from app.alerts.rules import matches
from app.models.entities import AlertRule, PipelineMetric, now
from app.schemas.observability import Observation
from app.services.observability_analysis import analyse, confirmation

EVENTS = {
    "CDC_CAPTURE_LAG_HIGH",
    "CDC_CONSUMER_BACKLOG_HIGH",
    "CDC_SINK_PROGRESS_STALLED",
    "CDC_SNAPSHOT_PROGRESS_STALLED",
    "CDC_OBSERVABILITY_UNAVAILABLE",
}


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_lag_ms: int = Field(default=300000, ge=1)
    backlog_records: int = Field(default=10000, ge=1)
    sink_stall_seconds: int = Field(default=300, ge=300, le=3600)
    snapshot_stall_seconds: int = Field(default=900, ge=900, le=3600)


async def observe(session, sample, state):
    rules = (await session.scalars(select(AlertRule).where(AlertRule.enabled.is_(True)))).all()
    analysis = dict(state.analysis_json)
    counters = dict(analysis.get("alerts", {}))
    history_rows = (
        await session.scalars(
            select(PipelineMetric)
            .where(
                PipelineMetric.pipeline_id == sample.pipeline_id,
                PipelineMetric.sample_bucket.is_not(None),
                PipelineMetric.created_at >= now() - timedelta(seconds=3600),
            )
            .order_by(PipelineMetric.created_at.desc())
            .limit(120)
        )
    ).all()
    history = [Observation.model_validate(r.values_json) for r in reversed(history_rows)]
    for rule in rules:
        explicit = EVENTS.intersection(rule.event_types or [])
        if not explicit:
            continue
        thresholds = Thresholds.model_validate(rule.config_json.get("observability", {}))
        findings = analyse(
            sample,
            history,
            stall_seconds=thresholds.sink_stall_seconds,
            snapshot_seconds=thresholds.snapshot_stall_seconds,
        )
        subjects = [None] + sample.deliveries
        for delivery in subjects:
            values = {}
            if delivery is None:
                m = sample.capture_lag
                values["CDC_CAPTURE_LAG_HIGH"] = (
                    m.value > thresholds.capture_lag_ms
                    if m.status == "AVAILABLE"
                    and sample.capture_expected_running
                    and sample.connector_state == "RUNNING"
                    else None
                )
                required = [sample.capture_lag, *(d.backlog for d in sample.deliveries)]
                required.extend(
                    d.sink_metrics["active_count"]
                    for d in sample.deliveries
                    if "active_count" in d.sink_metrics
                )
                values["CDC_OBSERVABILITY_UNAVAILABLE"] = any(
                    m.status in {"PARTIAL", "UNAVAILABLE", "INCONCLUSIVE", "STALE"}
                    for m in required
                )
                running = [s for s in sample.snapshots if s.state == "RUNNING"]
                snapshot_progress = all(
                    s.progress_counter
                    and s.progress_counter.status == "AVAILABLE"
                    and analysis.get("snapshot_counters", {}).get(str(s.table_id)) is not None
                    and s.progress_counter.value > analysis["snapshot_counters"][str(s.table_id)]
                    for s in running
                )
                completed = bool(sample.snapshots) and all(
                    s.state == "COMPLETED" for s in sample.snapshots
                )
                values["CDC_SNAPSHOT_PROGRESS_STALLED"] = (
                    True
                    if any(f.code == "SNAPSHOT_PROGRESS_STALLED" for f in findings)
                    else False
                    if completed or (running and snapshot_progress)
                    else None
                )
            else:
                m = delivery.backlog
                valid = m.status == "AVAILABLE" and delivery.expected_running
                values["CDC_CONSUMER_BACKLOG_HIGH"] = (
                    m.value > thresholds.backlog_records if valid else None
                )
                # A reset, pause, telemetry loss or identity change does not establish recovery.
                previous = analysis.get("offsets", {}).get(str(delivery.delivery_id))
                offsets = {
                    (p.topic + ":" + str(p.partition)): p.committed_offset
                    for p in delivery.partitions
                }
                progress_identity = [str(delivery.connector_id), delivery.group_id]
                progressing = (
                    (
                        previous
                        and previous.get("identity") == progress_identity
                        and previous.get("offsets", {}).keys() == offsets.keys()
                        and all(v >= previous["offsets"][k] for k, v in offsets.items())
                        and any(v > previous["offsets"][k] for k, v in offsets.items())
                    )
                    if valid
                    else False
                )
                stalled = next(
                    (
                        f
                        for f in findings
                        if f.code == "SINK_PROGRESS_STALLED"
                        and f.delivery_id == delivery.delivery_id
                    ),
                    None,
                )
                values["CDC_SINK_PROGRESS_STALLED"] = (
                    True
                    if stalled and valid
                    else False
                    if valid and (m.value == 0 or progressing)
                    else None
                )
            for event_type in explicit.intersection(values):
                identity = (
                    str(delivery.delivery_id)
                    if delivery
                    else str(sample.connector_id or sample.pipeline_id)
                )
                event = AlertEvent(
                    event_type=event_type,
                    severity="warning",
                    status="firing",
                    source_type="pipeline",
                    source_id=str(sample.source_id or sample.pipeline_id),
                    source_name=sample.name,
                    pipeline_id=sample.pipeline_id,
                    pipeline_name=sample.name,
                    component="observability",
                    message=f"{event_type}: measured CDC observation",
                    details={
                        "connector": identity,
                        "connector_name": delivery.connector_name
                        if delivery
                        else sample.connector_name,
                        "connector_id": str(
                            delivery.connector_id if delivery else sample.connector_id
                        ),
                        "observability_rule_id": str(rule.id),
                        "evidence": {
                            "capture_lag": sample.capture_lag.model_dump(mode="json"),
                            "backlog": delivery.backlog.model_dump(mode="json")
                            if delivery
                            else None,
                            "thresholds": thresholds.model_dump(),
                            "availability": [
                                {
                                    "delivery_id": str(d.delivery_id),
                                    "backlog_status": d.backlog.status,
                                    "backlog_reason": d.backlog.reason_code,
                                }
                                for d in sample.deliveries[:20]
                            ]
                            if event_type == "CDC_OBSERVABILITY_UNAVAILABLE"
                            else [],
                            "findings": [
                                f.model_dump(mode="json")
                                for f in findings
                                if f.delivery_id == (delivery.delivery_id if delivery else None)
                            ][:20],
                        },
                        "delivery_id": str(delivery.delivery_id) if delivery else None,
                        "observed_at": sample.collected_at.isoformat(),
                    },
                    occurred_at=sample.collected_at,
                )
                if not matches(rule, event):
                    continue
                status = confirmation(
                    counters,
                    f"{rule.id}:{event_type}:{identity}",
                    values[event_type],
                    sample.collected_at,
                )
                if status:
                    from dataclasses import replace

                    await AlertManager(session).handle(replace(event, status=status))
    offsets = dict(analysis.get("offsets", {}))
    for d in sample.deliveries:
        if d.backlog.status == "AVAILABLE":
            offsets[str(d.delivery_id)] = {
                "identity": [str(d.connector_id), d.group_id],
                "offsets": {
                    p.topic + ":" + str(p.partition): p.committed_offset for p in d.partitions
                },
            }
        else:
            offsets.pop(str(d.delivery_id), None)
    analysis.update(
        alerts=counters,
        offsets=offsets,
        snapshot_counters={
            str(s.table_id): s.progress_counter.value
            for s in sample.snapshots
            if s.progress_counter and s.progress_counter.status == "AVAILABLE"
        },
    )
    state.analysis_json = analysis
