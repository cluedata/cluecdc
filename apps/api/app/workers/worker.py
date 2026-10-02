import asyncio
from datetime import timedelta

import structlog
from sqlalchemy import select, update

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

log = structlog.get_logger()


async def run_job() -> bool:
    async with Session() as session:
        # Bounded operations finish within 5 minutes. Recover abandoned leases.
        await session.execute(
            update(Job)
            .where(
                Job.status == "RUNNING",
                Job.updated_at < now() - timedelta(minutes=5),
            )
            .values(status="PENDING")
        )
        job = await session.scalar(
            select(Job)
            .where(Job.status == "PENDING")
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            await session.commit()
            return False
        job.status = "RUNNING"
        audit(session, job.actor, "job.started", job)
        await session.commit()
        try:
            async with asyncio.timeout(240):
                if job.kind == "discover":
                    result = await discover(session, job.resource_id, job.actor)
                elif job.kind == "health":
                    result = await health(session, job.resource_id, job.actor)
                elif job.kind == "pipeline_operation":
                    result = await run_operation(session, job.resource_id)
                else:
                    raise DomainError("UNKNOWN_JOB", "Unknown job type")
            job.status = "COMPLETED"
            job.result_json = result
            audit(session, job.actor, "job.completed", job)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await session.refresh(job)
            job.status = "FAILED"
            job.error = (
                exc.message
                if isinstance(exc, DomainError)
                else "Operation failed; inspect correlation logs"
            )
            audit(session, job.actor, "job.failed", job)
            await session.commit()
            log.warning("job_failed", job_id=str(job.id), kind=job.kind)
        return True


async def worker_loop():
    next_reconcile = 0.0
    while True:
        try:
            processed = await run_job()
            if asyncio.get_running_loop().time() >= next_reconcile:
                async with Session() as session:
                    # Lock runtime observations against user lifecycle operations.
                    pipelines = (
                        await session.scalars(
                            select(Pipeline)
                            .where(Pipeline.connector_id.is_not(None))
                            .with_for_update(skip_locked=True)
                            .limit(100)
                        )
                    ).all()
                    for pipeline in pipelines:
                        await reconcile(session, pipeline)
                    await session.commit()
                async with Session() as session:
                    links = (
                        await session.scalars(
                            select(PipelineDestination).with_for_update(skip_locked=True).limit(100)
                        )
                    ).all()
                    for link in links:
                        await reconcile_delivery(session, link)
                    await sync_snapshot_operations(session)
                    await session.commit()
                next_reconcile = (
                    asyncio.get_running_loop().time() + get_settings().reconcile_interval_seconds
                )
            async with Session() as session:
                try:
                    processed = bool(await dispatch_pending(session)) or processed
                except Exception as exc:
                    await session.rollback()
                    log.error(
                        "notification_dispatch_iteration_failed",
                        error_type=type(exc).__name__,
                    )
            await asyncio.sleep(0.2 if processed else 1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("worker_iteration_failed")
            await asyncio.sleep(3)
