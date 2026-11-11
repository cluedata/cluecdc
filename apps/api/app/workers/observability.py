"""Independent bounded scheduler, PostgreSQL claims, fencing and bucket idempotency."""

import asyncio
import time
import uuid
from datetime import timedelta

import structlog
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import Session
from app.models.entities import ObservabilityCollectionState as State
from app.models.entities import Pipeline, PipelineMetric, now
from app.services import observability as service
from app.services.observability_analysis import utc

log = structlog.get_logger(component="observability")


async def seed(session):
    ids = (
        await session.scalars(
            select(Pipeline.id)
            .outerjoin(State, State.pipeline_id == Pipeline.id)
            .where(State.pipeline_id.is_(None))
            .order_by(Pipeline.created_at)
            .limit(100)
        )
    ).all()
    if ids:
        timestamp = (
            await session.scalar(select(func.clock_timestamp()))
            if session.bind.dialect.name == "postgresql"
            else now()
        )
        insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
        await session.execute(
            insert(State)
            .values(
                [
                    dict(
                        pipeline_id=i,
                        next_due=timestamp,
                        failures=0,
                        latest_json={},
                        analysis_json={},
                    )
                    for i in ids
                ]
            )
            .on_conflict_do_nothing(index_elements=["pipeline_id"])
        )


async def claim(session, owner):
    timestamp = now()
    if session.bind.dialect.name == "postgresql":
        # Serialize only the short claim transaction, never remote provider work.
        # All worker processes share the same four-collection resource ceiling.
        await session.execute(text("SELECT pg_advisory_xact_lock(731947230035)"))
        timestamp = await session.scalar(select(func.clock_timestamp()))
    active = await session.scalar(
        select(func.count()).select_from(State).where(State.lease_expires_at > timestamp)
    )
    if active >= get_settings().observability_concurrency:
        await session.commit()
        return None
    state = await session.scalar(
        select(State)
        .where(
            State.next_due <= timestamp,
            or_(State.lease_expires_at.is_(None), State.lease_expires_at <= timestamp),
        )
        .order_by(State.next_due, State.pipeline_id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if state is None:
        return None
    state.lease_owner = owner
    state.lease_expires_at = timestamp + timedelta(seconds=35)
    interval = get_settings().observability_interval_seconds
    bucket = timestamp.fromtimestamp(
        int(timestamp.timestamp()) // interval * interval, timestamp.tzinfo
    )
    await session.commit()
    return state.pipeline_id, bucket


async def finish(session, identifier, bucket, owner, sample, duration, failed):
    timestamp = (
        await session.scalar(select(func.clock_timestamp()))
        if session.bind.dialect.name == "postgresql"
        else now()
    )
    state = await session.scalar(
        select(State).where(State.pipeline_id == identifier).with_for_update()
    )
    if not state or state.lease_owner != owner or utc(state.lease_expires_at) <= utc(timestamp):
        return False  # Fenced: a collector that lost its lease may never write or alert.
    existing = await session.scalar(
        select(PipelineMetric.id).where(
            PipelineMetric.pipeline_id == identifier, PipelineMetric.sample_bucket == bucket
        )
    )
    if existing is None:
        async with session.begin_nested():
            session.add(
                PipelineMetric(
                    pipeline_id=identifier,
                    sample_bucket=bucket,
                    values_json=sample.model_dump(mode="json"),
                )
            )
            await session.flush()
        from app.alerts.observability import observe

        await observe(session, sample, state)
        state.latest_json = sample.model_dump(mode="json")
        health = dict(state.analysis_json)
        health["collections_failed" if failed else "collections_succeeded"] = (
            health.get("collections_failed" if failed else "collections_succeeded", 0) + 1
        )
        health["last_duration_seconds"] = duration
        state.analysis_json = health
        if not failed:
            state.last_success_at = sample.collected_at
    state.failures = min(8, state.failures + 1) if failed else 0
    delay = min(300, get_settings().observability_interval_seconds * 2**state.failures)
    state.next_due = timestamp + timedelta(seconds=delay)
    state.lease_owner = None
    state.lease_expires_at = None
    await session.commit()
    return True


async def run_once():
    owner = uuid.uuid4().hex
    async with Session() as session:
        claimed = await claim(session, owner)
    if claimed is None:
        return False
    identifier, bucket = claimed
    started = time.monotonic()
    try:
        async with Session() as session:
            pipeline = await session.get(Pipeline, identifier)
            if pipeline is None:
                return True
            sample = await service.empty(session, pipeline)
            failed = False
            try:
                async with asyncio.timeout(get_settings().observability_budget_seconds):
                    sample = await service.collect(session, pipeline, result=sample)
            except Exception:
                # Partial evidence survives provider timeout. Do not leak exception strings.
                failed = True
                await session.rollback()
                sample.status = "PARTIAL"
                sample.collected_at = now()
                sample.last_success_at = None
            await session.rollback()  # collection never persists CDC state
        async with Session() as session:
            await finish(
                session, identifier, bucket, owner, sample, time.monotonic() - started, failed
            )
    except asyncio.CancelledError:
        # Lease expiry provides crash/shutdown recovery without advancing the schedule.
        raise
    except Exception as exc:
        log.warning("collection_failed", error_type=type(exc).__name__)
    return True


async def collector_loop():
    next_cleanup = 0.0
    while True:
        try:
            if get_settings().observability_enabled:
                async with Session() as session:
                    await seed(session)
                    if time.monotonic() >= next_cleanup:
                        await service.cleanup(session)
                        next_cleanup = time.monotonic() + 60
                    await session.commit()
                await asyncio.gather(
                    *(run_once() for _ in range(get_settings().observability_concurrency))
                )
        except asyncio.CancelledError:
            raise
        except IntegrityError:
            log.warning("collection_conflict")
        except Exception as exc:
            log.warning("scheduler_failed", error_type=type(exc).__name__)
        await asyncio.sleep(1)
