import asyncio
import json
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import DateTime, delete, func, literal, literal_column, select, update

from app.core.errors import DomainError
from app.models.entities import (
    DataQualityCheck,
    DataQualityRule,
    DataQualityRuleResult,
    DataQualityRun,
    Job,
    NotificationChannel,
    now,
)
from app.repositories.metadata import audit, get, serialize
from app.schemas.data_quality import CheckInput
from app.services import data_quality_engine as engine
from app.services.data_quality_alerts import channel_status, observe


def json_safe(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


async def check(session, identifier):
    item = await get(session, DataQualityCheck, identifier)
    if item.deleted_at:
        raise DomainError("NOT_FOUND", "Data Quality check was deleted", 404)
    return item


async def view(session, item):
    result = serialize(item)
    result["configuration"] = result.pop("config_json")
    result["alert_status"] = await channel_status(session, item.config_json)
    latest = await session.scalar(
        select(DataQualityRun)
        .where(DataQualityRun.check_id == item.id)
        .order_by(DataQualityRun.created_at.desc())
        .limit(1)
    )
    result["latest_run"] = serialize(latest) if latest else None
    # Snapshots and alert counters are detail-only; keep list responses bounded.
    if result["latest_run"]:
        result["latest_run"].pop("snapshot_json", None)
    result.pop("alert_state_json", None)
    from app.models.entities import Connection

    for side in ("source", "destination"):
        connection = await session.get(Connection, getattr(item, f"{side}_connection_id"))
        result[f"{side}_name"] = connection.name if connection else "Unavailable connection"
    from app.models.entities import Pipeline, PipelineDestination, PipelineTable

    config = item.config_json
    related = (
        await session.execute(
            select(Pipeline, PipelineDestination)
            .join(PipelineDestination, PipelineDestination.pipeline_id == Pipeline.id)
            .join(PipelineTable, PipelineTable.pipeline_id == Pipeline.id)
            .where(
                Pipeline.source_id == item.source_connection_id,
                PipelineDestination.destination_id == item.destination_connection_id,
                PipelineTable.schema_name == config["source"]["schema_name"],
                PipelineTable.table_name == config["source"]["table"],
                PipelineTable.removed_at.is_(None),
            )
        )
    ).all()
    result["related_pipelines"] = [
        {"id": str(p.id), "name": p.name}
        for p, delivery in related
        if any(
            m.get("table_name") == config["destination"]["table"]
            and m.get("schema_name") == config["destination"]["schema_name"]
            for m in delivery.topic_mapping_json
        )
    ]
    return result


async def save(session, data: CheckInput, actor: str, item=None):
    _, _, capability = await engine.inspect(session, data, now())
    if capability["status"] != "SUPPORTED":
        raise DomainError(
            "DQ_CONFIGURATION_REQUIRED", "Resolve rule capability requirements", 422, capability
        )
    old_rules = {r["type"]: r["id"] for r in item.config_json["rules"]} if item else {}
    for rule in data.rules:
        expected = old_rules.get(rule.type)
        if rule.id and str(rule.id) != expected:
            raise DomainError(
                "DQ_RULE_ID_INVALID", "Rule identity does not belong to this check", 422
            )
        rule.id = UUID(expected) if expected else uuid4()
    channel = (
        await session.get(NotificationChannel, data.alerting.channel_id)
        if data.alerting.channel_id
        else None
    )
    if data.alerting.enabled is None:
        data.alerting.enabled = bool(channel and channel.enabled)
    if data.alerting.enabled and (not channel or not channel.enabled):
        # Keep an already configured channel when it becomes unavailable; surface its state.
        old_channel = item.config_json["alerting"].get("channel_id") if item else None
        if str(data.alerting.channel_id) != old_channel:
            raise DomainError(
                "DQ_CHANNEL_UNAVAILABLE", "Select an enabled notification channel", 422
            )
    before = serialize(item) if item else None
    if item is None:
        item = DataQualityCheck(created_by=actor)
        session.add(item)
        item.version = 1
    else:
        item.version += 1
    item.name, item.description = data.name, data.description
    item.source_connection_id = data.source.connection_id
    item.destination_connection_id = data.destination.connection_id
    item.pipeline_id = data.pipeline_id
    item.enabled = data.enabled
    item.updated_by = actor
    item.config_json = data.model_dump(mode="json")
    item.next_run_at = (
        now() + timedelta(seconds=data.schedule.frequency_seconds)
        if (data.enabled and data.schedule.enabled)
        else None
    )
    await session.flush()
    await session.execute(delete(DataQualityRule).where(DataQualityRule.check_id == item.id))
    for index, rule in enumerate(data.rules):
        session.add(
            DataQualityRule(
                id=rule.id,
                check_id=item.id,
                rule_type=rule.type,
                config_json=rule.model_dump(mode="json"),
                enabled=rule.enabled,
                position=index,
            )
        )
    await session.flush()
    audit(
        session, actor, "data_quality.updated" if before else "data_quality.created", item, before
    )
    return item


async def enqueue(session, item, actor, trigger="MANUAL", scheduled_at=None, request_key=None):
    timestamp = scheduled_at or now()
    occurrence = (
        f"scheduled:{engine.aware(timestamp).isoformat()}"
        if trigger == "SCHEDULED"
        else f"manual:{request_key or uuid4()}"
    )
    existing = await session.scalar(
        select(DataQualityRun).where(
            DataQualityRun.check_id == item.id, DataQualityRun.occurrence == occurrence
        )
    )
    if existing:
        return existing
    run_id = uuid4()
    claimed = await session.execute(
        update(DataQualityCheck)
        .where(
            DataQualityCheck.id == item.id,
            DataQualityCheck.active_run_id.is_(None),
            DataQualityCheck.deleted_at.is_(None),
            DataQualityCheck.version == item.version,
        )
        .values(active_run_id=run_id)
        .returning(DataQualityCheck.id)
    )
    if claimed.scalar_one_or_none() is None:
        raise DomainError("DQ_CHECK_BUSY", "A run is already active or configuration changed", 409)
    job = Job(kind="data_quality", resource_id=run_id, actor=actor)
    session.add(job)
    await session.flush()
    run = DataQualityRun(
        id=run_id,
        check_id=item.id,
        job_id=job.id,
        trigger=trigger,
        occurrence=occurrence,
        scheduled_at=timestamp,
        version=item.version,
        snapshot_json=item.config_json,
        window_json=engine.evaluation_window(
            CheckInput.model_validate(item.config_json), timestamp
        ),
    )
    session.add(run)
    await session.flush()
    audit(session, actor, "data_quality.queued", run)
    return run


async def schedule_due(session, timestamp=None):
    timestamp = timestamp or now()
    # Reap exhausted/cancelled jobs, never leave a Check permanently blocked after restart.
    abandoned = (
        await session.scalars(
            select(DataQualityRun)
            .join(Job)
            .where(
                DataQualityRun.status.in_(["PENDING", "RUNNING"]),
                Job.status.in_(["FAILED", "CANCELLED"]),
            )
            .with_for_update(skip_locked=True)
            .limit(100)
        )
    ).all()
    for run in abandoned:
        item = await session.get(DataQualityCheck, run.check_id)
        run.status, run.quality, run.finished_at = "FAILED", "ERROR", timestamp
        run.error_code = "DQ_WORKER_ATTEMPTS_EXHAUSTED"
        results = [
            {
                "rule_id": r["id"],
                "rule_type": r["type"],
                "quality": "ERROR",
                "error_code": run.error_code,
                "consistency": "BEST_EFFORT",
            }
            for r in run.snapshot_json["rules"]
            if r["enabled"]
        ]
        await persist_results(session, item, run, results)
        await session.execute(
            update(DataQualityCheck)
            .where(DataQualityCheck.id == run.check_id, DataQualityCheck.active_run_id == run.id)
            .values(active_run_id=None)
        )
    items = (
        await session.scalars(
            select(DataQualityCheck)
            .where(
                DataQualityCheck.deleted_at.is_(None),
                DataQualityCheck.enabled.is_(True),
                DataQualityCheck.next_run_at <= timestamp,
            )
            .order_by(DataQualityCheck.next_run_at)
            .with_for_update(skip_locked=True)
            .limit(100)
        )
    ).all()
    count = 0
    for item in items:
        scheduled_at = item.next_run_at
        frequency = item.config_json["schedule"]["frequency_seconds"]
        # CAS protects SQLite tests and workers that saw the same due occurrence.
        claimed = await session.execute(
            update(DataQualityCheck)
            .where(DataQualityCheck.id == item.id, DataQualityCheck.next_run_at == scheduled_at)
            .values(next_run_at=timestamp + timedelta(seconds=frequency))
            .returning(DataQualityCheck.id)
        )
        if claimed.scalar_one_or_none() is None:
            continue
        if not item.active_run_id:
            await enqueue(session, item, "data-quality-scheduler", "SCHEDULED", scheduled_at)
            count += 1
        else:
            audit(session, "data-quality-scheduler", "data_quality.overlap_skipped", item)
    return count


async def persist_results(session, item, run, results):
    for result in results:
        session.add(
            DataQualityRuleResult(
                run_id=run.id,
                rule_id=UUID(result["rule_id"]),
                rule_type=result["rule_type"],
                quality=result["quality"],
                evidence_json=json_safe(result),
            )
        )
    await observe(session, item, run, results, CheckInput.model_validate(run.snapshot_json))


async def run_job(session, run_id, job_id, owner):
    run = await get(session, DataQualityRun, run_id)
    if run.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return {"run_id": str(run.id), "quality": run.quality}
    config = CheckInput.model_validate(run.snapshot_json)
    run.status, run.started_at = "RUNNING", now()
    audit(session, "data-quality-worker", "data_quality.started", run)
    await session.commit()
    try:
        async with asyncio.timeout(config.schedule.timeout_seconds):
            results, window = await engine.execute(session, config, engine.aware(run.scheduled_at))
        status, error = "COMPLETED", None
    except Exception as exc:
        # Aggregate scans are not automatically retried on query/permission/timeout failure.
        error = (
            "DQ_TIMEOUT"
            if isinstance(exc, TimeoutError)
            else (exc.code if isinstance(exc, DomainError) else "DQ_EXECUTION_ERROR")
        )
        status, window = "FAILED", run.window_json
        results = [
            {
                "rule_id": str(r.id),
                "rule_type": r.type,
                "quality": "ERROR" if r.enabled else "SKIPPED",
                "error_code": error,
                "consistency": "BEST_EFFORT",
                "limitations": engine.LIMITATIONS,
            }
            for r in config.rules
        ]
    # No partial results or notifications can commit after losing Job ownership.
    await session.rollback()
    job = await session.scalar(
        select(Job)
        .where(
            Job.id == job_id,
            Job.claimed_by == owner,
            Job.status == "RUNNING",
            Job.lease_expires_at > now(),
        )
        .with_for_update()
    )
    if not job:
        raise DomainError("WORKER_LEASE_EXPIRED", "Data Quality worker lost ownership", 503)
    run = await get(session, DataQualityRun, run_id)
    if run.status in {"COMPLETED", "FAILED", "CANCELLED"}:
        return {"run_id": str(run.id), "quality": run.quality}
    item = await session.scalar(
        select(DataQualityCheck).where(DataQualityCheck.id == run.check_id).with_for_update()
    )
    if item.deleted_at:
        run.status, run.quality = "CANCELLED", "INCONCLUSIVE"
    else:
        run.status, run.quality = status, engine.aggregate([r["quality"] for r in results])
        await persist_results(session, item, run, results)
    run.finished_at, run.window_json, run.error_code = now(), window, error
    item.active_run_id = None
    audit(session, job.actor, "data_quality.finished", run)
    return {"run_id": str(run.id), "quality": run.quality}


async def cleanup(session, timestamp=None):
    timestamp = timestamp or now()
    days = DataQualityCheck.config_json["schedule"]["retention_days"].as_integer()
    if session.get_bind().dialect.name == "sqlite":
        expired = func.julianday(DataQualityRun.finished_at) < func.julianday(timestamp) - days
    else:
        cutoff = literal(timestamp, DateTime(timezone=True)) - days * literal_column(
            "INTERVAL '1 day'"
        )
        expired = DataQualityRun.finished_at < cutoff
    ids = (
        await session.scalars(
            select(DataQualityRun.id)
            .join(DataQualityCheck)
            .where(
                DataQualityRun.status.in_(["COMPLETED", "FAILED", "CANCELLED"]),
                expired,
            )
            .order_by(DataQualityRun.finished_at)
            .limit(1000)
        )
    ).all()
    if ids:
        await session.execute(delete(DataQualityRun).where(DataQualityRun.id.in_(ids)))
