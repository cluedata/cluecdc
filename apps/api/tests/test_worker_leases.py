import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import DomainError
from app.models.entities import Base, Job, now
from app.workers import worker


async def queued_job(factory, **kwargs):
    async with factory() as db:
        job = Job(kind="health", resource_id=uuid4(), actor="test", **kwargs)
        db.add(job)
        await db.commit()
        return job.id


async def test_expired_lease_is_recovered_but_active_lease_is_not(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    expired = await queued_job(
        db_factory,
        status="RUNNING",
        claimed_by="dead-worker",
        lease_expires_at=now() - timedelta(seconds=1),
        attempt_count=1,
    )
    active = await queued_job(
        db_factory,
        status="RUNNING",
        claimed_by="live-worker",
        lease_expires_at=now() + timedelta(minutes=5),
        attempt_count=1,
    )
    claim = await worker._claim_job("replacement-worker")
    assert claim[0] == expired
    assert await worker._claim_job("other-worker") is None
    async with db_factory() as db:
        recovered = await db.get(Job, expired)
        assert recovered.claimed_by == "replacement-worker" and recovered.attempt_count == 2
        assert (await db.get(Job, active)).claimed_by == "live-worker"


async def test_retry_backoff_and_terminal_failure(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    identifier = await queued_job(db_factory)
    for attempt in range(1, 4):
        assert (await worker._claim_job())[0] == identifier
        await worker._fail_job(identifier, RuntimeError("secret-in-exception"))
        async with db_factory() as db:
            job = await db.get(Job, identifier)
            assert "secret-in-exception" not in job.error
            assert job.attempt_count == attempt
            assert job.claimed_by is None and job.lease_expires_at is None
            assert job.status == ("FAILED" if attempt == 3 else "PENDING")
            if attempt < 3:
                assert await worker._claim_job() is None
                job.next_attempt_at = now() - timedelta(seconds=1)
                await db.commit()


async def test_success_commits_the_operation_result_and_clears_the_lease(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    identifier = await queued_job(db_factory)

    async def health(session, resource_id, actor):
        assert not session.in_transaction()
        session.add(Job(kind="followup", resource_id=resource_id, actor=actor))
        return {"verified": True}

    monkeypatch.setattr(worker, "health", health)
    assert await worker.run_job()
    async with db_factory() as db:
        job = await db.get(Job, identifier)
        assert job.status == "COMPLETED" and job.result_json == {"verified": True}
        assert job.claimed_by is None and job.lease_expires_at is None
        assert await db.scalar(select(Job.id).where(Job.kind == "followup"))


async def test_graceful_cancellation_releases_the_claim(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    identifier = await queued_job(db_factory)
    started = asyncio.Event()

    async def health(*_):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "health", health)
    task = asyncio.create_task(worker.run_job())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with db_factory() as db:
        job = await db.get(Job, identifier)
        assert job.status == "PENDING"
        assert job.claimed_by is None and job.lease_expires_at is None


@pytest.fixture
async def postgres_factory():
    url = os.getenv("WORKER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WORKER_TEST_DATABASE_URL to run PostgreSQL SKIP LOCKED concurrency tests")
    schema = "cluecdc_worker_test_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        # schema is constructed solely from a fixed prefix and UUID; no user paths/data are removed.
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_two_postgres_workers_racing_claim_one_job(postgres_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", postgres_factory)
    identifier = await queued_job(postgres_factory)
    claims = await asyncio.gather(worker._claim_job("worker-a"), worker._claim_job("worker-b"))
    assert sum(claim is not None for claim in claims) == 1
    assert next(claim for claim in claims if claim)[0] == identifier
    async with postgres_factory() as db:
        job = await db.get(Job, identifier)
        assert job.claimed_by in {"worker-a", "worker-b"}
        assert job.status == "RUNNING" and job.attempt_count == 1
    # A third worker cannot complete another worker's lease.
    await worker._finish_job(identifier, {"incorrect_owner": True})
    async with postgres_factory() as db:
        assert (await db.get(Job, identifier)).status == "RUNNING"


async def test_domain_error_is_reported_without_raw_exception_context(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    identifier = await queued_job(db_factory)
    assert await worker._claim_job()
    await worker._fail_job(identifier, DomainError("SAFE_CODE", "Authentication was rejected", 422))
    async with db_factory() as db:
        assert (await db.get(Job, identifier)).error == "Authentication was rejected"


async def test_lease_renewal_keeps_long_running_job_owned(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(job_lease_seconds=3, job_max_attempts=3)
    )
    identifier = await queued_job(db_factory)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def health(*_):
        started.set()
        await finish.wait()
        return {"renewed": True}

    monkeypatch.setattr(worker, "health", health)
    task = asyncio.create_task(worker.run_job())
    try:
        await asyncio.wait_for(started.wait(), 5)
        async with db_factory() as db:
            first_expiry = (await db.get(Job, identifier)).lease_expires_at
        await asyncio.sleep(1.5)
        async with db_factory() as db:
            assert (await db.get(Job, identifier)).lease_expires_at > first_expiry
        assert await worker._claim_job("second-worker") is None
        finish.set()
        assert await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_losing_lease_cancels_external_work_without_overwriting_new_owner(
    db_factory, monkeypatch
):
    monkeypatch.setattr(worker, "Session", db_factory)
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(job_lease_seconds=3, job_max_attempts=3)
    )
    identifier = await queued_job(db_factory)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def health(*_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(worker, "health", health)
    task = asyncio.create_task(worker.run_job())
    try:
        await asyncio.wait_for(started.wait(), 5)
        async with db_factory() as db:
            job = await db.get(Job, identifier)
            job.claimed_by = "replacement-worker"
            await db.commit()
        assert await asyncio.wait_for(task, 5)
        assert cancelled.is_set()
        async with db_factory() as db:
            job = await db.get(Job, identifier)
            assert job.status == "RUNNING" and job.claimed_by == "replacement-worker"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
