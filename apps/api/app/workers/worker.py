import asyncio
import os
import socket
import uuid
from datetime import timedelta

import structlog
from sqlalchemy import or_, select, update

from app.alerts.dispatcher import dispatch_pending
from app.core.config import get_settings
from app.core.database import Session
from app.core.errors import DomainError
from app.models.entities import Job, Pipeline, PipelineDestination, now
from app.repositories.metadata import audit
from app.services.destinations.service import reconcile as reconcile_delivery
from app.services.pipeline import reconcile
from app.services.pipeline_tables import run_operation, sync_snapshot_operations
from app.services.source import discover, health

log = structlog.get_logger(component="worker")
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def _claim_job(worker_id: str = WORKER_ID) -> tuple[uuid.UUID, str, uuid.UUID, str] | None:
    settings = get_settings()
    timestamp = now()
    async with Session() as session:
        await session.execute(
            update(Job)
            .where(
                Job.status == "RUNNING",
                Job.lease_expires_at < timestamp,
                Job.attempt_count >= settings.job_max_attempts,
            )
            .values(
                status="FAILED",
                claimed_by=None,
                lease_expires_at=None,
                error="Job lease expired after the maximum number of attempts",
            )
        )
        await session.execute(
            update(Job)
            .where(
                Job.status == "RUNNING",
                Job.lease_expires_at < timestamp,
                Job.attempt_count < settings.job_max_attempts,
            )
            .values(
                status="PENDING",
                claimed_by=None,
                lease_expires_at=None,
                next_attempt_at=timestamp,
            )
        )
        job = await session.scalar(
            select(Job)
            .where(
                Job.status == "PENDING",
                Job.next_attempt_at <= timestamp,
                or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < timestamp),
            )
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            await session.commit()
            return None
        result = await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "PENDING")
            .values(
                status="RUNNING",
                claimed_by=worker_id,
                lease_expires_at=timestamp + timedelta(seconds=settings.job_lease_seconds),
                attempt_count=Job.attempt_count + 1,
            )
            .returning(Job.id)
        )
        if result.scalar_one_or_none() is None:
            await session.commit()
            return None
        await session.refresh(job)
        audit(session, job.actor, "job.started", job)
        claimed = (job.id, job.kind, job.resource_id, job.actor)
        await session.commit()
        return claimed


async def _finish_job(job_id: uuid.UUID, result: dict) -> None:
    async with Session() as session:
        job = await session.scalar(
            select(Job).where(Job.id == job_id, Job.claimed_by == WORKER_ID).with_for_update()
        )
        if job is None:
            return
        job.status = "COMPLETED"
        job.result_json = result
        job.error = None
        job.claimed_by = None
        job.lease_expires_at = None
        audit(session, job.actor, "job.completed", job)
        await session.commit()


async def _fail_job(job_id: uuid.UUID, exc: Exception) -> None:
    settings = get_settings()
    async with Session() as session:
        job = await session.scalar(
            select(Job).where(Job.id == job_id, Job.claimed_by == WORKER_ID).with_for_update()
        )
        if job is None:
            return
        job.error = (
            exc.message
            if isinstance(exc, DomainError)
            else "Operation failed; inspect correlation logs"
        )
        job.claimed_by = None
        job.lease_expires_at = None
        if job.attempt_count < settings.job_max_attempts:
            job.status = "PENDING"
            job.next_attempt_at = now() + timedelta(seconds=min(60, 2**job.attempt_count))
            audit(session, job.actor, "job.retry_scheduled", job)
        else:
            job.status = "FAILED"
            audit(session, job.actor, "job.failed", job)
        await session.commit()


async def _release_job(job_id: uuid.UUID) -> None:
    async with Session() as session:
        job = await session.scalar(
            select(Job).where(Job.id == job_id, Job.claimed_by == WORKER_ID).with_for_update()
        )
        if job is not None:
            job.status = "PENDING"
            job.claimed_by = None
            job.lease_expires_at = None
            job.next_attempt_at = now()
            job.attempt_count = max(0, job.attempt_count - 1)
            await session.commit()


async def _renew_lease(
    job_id: uuid.UUID, owner_task: asyncio.Task, lease_lost: asyncio.Event
) -> None:
    interval = max(1, get_settings().job_lease_seconds // 3)
    while True:
        await asyncio.sleep(interval)
        try:
            async with Session() as session:
                result = await session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.claimed_by == WORKER_ID, Job.status == "RUNNING")
                    .values(
                        lease_expires_at=now() + timedelta(seconds=get_settings().job_lease_seconds)
                    )
                    .returning(Job.id)
                )
                owned = result.scalar_one_or_none() is not None
                await session.commit()
        except Exception:
            owned = False
        if not owned:
            # Stop work immediately when ownership cannot be renewed.
            lease_lost.set()
            owner_task.cancel()
            return


async def run_job() -> bool:
    claimed = await _claim_job()
    if claimed is None:
        return False
    job_id, kind, resource_id, actor = claimed
    owner_task = asyncio.current_task()
    assert owner_task is not None
    lease_lost = asyncio.Event()
    renewer = asyncio.create_task(_renew_lease(job_id, owner_task, lease_lost))
    try:
        async with Session() as session, asyncio.timeout(240):
            if kind == "discover":
                result = await discover(session, resource_id, actor)
            elif kind == "health":
                result = await health(session, resource_id, actor)
            elif kind == "pipeline_operation":
                result = await run_operation(session, resource_id)
            else:
                raise DomainError("UNKNOWN_JOB", "Unknown job type")
            await session.commit()
        await _finish_job(job_id, result)
    except asyncio.CancelledError:
        await asyncio.shield(_release_job(job_id))
        if lease_lost.is_set():
            log.warning("job_lease_lost", job_id=str(job_id))
            return True
        raise
    except Exception as exc:
        await _fail_job(job_id, exc)
        log.warning("job_failed", job_id=str(job_id), kind=kind)
    finally:
        renewer.cancel()
        try:
            await renewer
        except asyncio.CancelledError:
            pass
    return True


async def _reconcile_batch() -> None:
    async with Session() as session:
        pipeline_ids = list(
            (
                await session.scalars(
                    select(Pipeline.id)
                    .where(Pipeline.connector_id.is_not(None))
                    .order_by(Pipeline.updated_at)
                    .limit(100)
                )
            ).all()
        )
        delivery_ids = list(
            (
                await session.scalars(
                    select(PipelineDestination.id)
                    .order_by(PipelineDestination.updated_at)
                    .limit(100)
                )
            ).all()
        )
        await session.commit()
    semaphore = asyncio.Semaphore(get_settings().worker_concurrency)

    async def pipeline_task(identifier: uuid.UUID) -> None:
        async with semaphore, Session() as session:
            pipeline = await session.get(Pipeline, identifier)
            if pipeline is not None:
                await reconcile(session, pipeline)
                pipeline.updated_at = now()
                await session.commit()

    async def delivery_task(identifier: uuid.UUID) -> None:
        async with semaphore, Session() as session:
            delivery = await session.get(PipelineDestination, identifier)
            if delivery is not None:
                await reconcile_delivery(session, delivery)
                delivery.updated_at = now()
                await session.commit()

    async with asyncio.TaskGroup() as group:
        for identifier in pipeline_ids:
            group.create_task(pipeline_task(identifier))
        for identifier in delivery_ids:
            group.create_task(delivery_task(identifier))
    async with Session() as session:
        await sync_snapshot_operations(session)
        await session.commit()


async def worker_loop() -> None:
    next_reconcile = 0.0
    concurrency = get_settings().worker_concurrency
    while True:
        try:
            async with asyncio.TaskGroup() as group:
                jobs = [group.create_task(run_job()) for _ in range(concurrency)]
            processed = any(job.result() for job in jobs)
            if asyncio.get_running_loop().time() >= next_reconcile:
                await _reconcile_batch()
                next_reconcile = (
                    asyncio.get_running_loop().time() + get_settings().reconcile_interval_seconds
                )
            async with Session() as session:
                try:
                    processed = bool(await dispatch_pending(session)) or processed
                except Exception as exc:
                    await session.rollback()
                    log.error(
                        "notification_dispatch_iteration_failed", error_type=type(exc).__name__
                    )
            await asyncio.sleep(0.2 if processed else 1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("worker_iteration_failed", error_type=type(exc).__name__)
            await asyncio.sleep(3)
