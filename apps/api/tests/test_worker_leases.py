import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import DomainError
from app.models.entities import (
    Base,
    ConnectCluster,
    Connector,
    Job,
    KafkaCluster,
    Pipeline,
    PipelineDestination,
    Source,
    now,
)
from app.workers import worker
from app.workers.retry import classify_failure, retry_delay_seconds


async def queued_job(factory, **kwargs):
    async with factory() as db:
        job = Job(kind="health", resource_id=uuid4(), actor="test", **kwargs)
        db.add(job)
        await db.commit()
        return job.id


async def test_automatic_infrastructure_health_checks_are_persisted(db_factory, monkeypatch):
    async with db_factory() as db:
        kafka = KafkaCluster(name="health-kafka", bootstrap_servers="kafka:9092")
        db.add(kafka)
        await db.flush()
        connect = ConnectCluster(
            name="health-connect",
            base_url="http://connect:8083",
            kafka_cluster_id=kafka.id,
        )
        db.add(connect)
        await db.commit()
        kafka_id, connect_id = kafka.id, connect.id

    async def kafka_health(session, cluster):
        cluster.status = "HEALTHY"
        cluster.status_reason = "Kafka connection succeeded"
        cluster.last_health_check_at = now()
        return {"status": "HEALTHY"}

    async def connect_health(session, cluster):
        cluster.status = "UNHEALTHY"
        cluster.status_reason = "Kafka Connect is unavailable or timed out"
        cluster.last_health_check_at = now()
        return {"status": "UNHEALTHY"}

    monkeypatch.setattr(worker, "Session", db_factory)
    monkeypatch.setattr(worker, "check_kafka", kafka_health)
    monkeypatch.setattr(worker, "check_connect", connect_health)

    await worker._infrastructure_health_batch()

    async with db_factory() as db:
        kafka = await db.get(KafkaCluster, kafka_id)
        connect = await db.get(ConnectCluster, connect_id)
        assert kafka.status == "HEALTHY"
        assert kafka.last_health_check_at is not None
        assert connect.status == "UNHEALTHY"
        assert connect.status_reason == "Kafka Connect is unavailable or timed out"


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
    assert await worker._claim_job("replacement-worker") is None
    async with db_factory() as db:
        recovered = await db.get(Job, expired)
        assert recovered.status == "PENDING"
        assert recovered.error_code == "WORKER_LEASE_EXPIRED"
        assert recovered.error_category == "TRANSIENT_CONNECTIVITY"
        assert recovered.failure_history_json[-1]["attempt"] == 1
        recovered.next_attempt_at = now() - timedelta(seconds=1)
        await db.commit()
    claim = await worker._claim_job("replacement-worker")
    assert claim is not None and claim[0] == expired
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
            assert job.error_code == "UNEXPECTED_ERROR"
            assert job.error_category == "UNKNOWN"
            assert job.retryable is True
            assert job.last_attempt_at is not None
            assert len(job.failure_history_json) == attempt
            assert job.claimed_by is None and job.lease_expires_at is None
            assert job.status == ("FAILED" if attempt == 3 else "PENDING")
            if attempt < 3:
                assert await worker._claim_job() is None
                job.next_attempt_at = now() - timedelta(seconds=1)
                await db.commit()


async def test_permanent_failure_is_not_automatically_retried(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    identifier = await queued_job(db_factory)
    assert await worker._claim_job()
    await worker._fail_job(
        identifier,
        DomainError("SOURCE_AUTH_FAILED", "Source rejected the supplied credentials", 422),
    )
    async with db_factory() as db:
        job = await db.get(Job, identifier)
        assert job.status == "FAILED" and job.attempt_count == 1
        assert job.error_category == "AUTHENTICATION" and job.retryable is False
        assert job.failure_history_json[-1]["code"] == "SOURCE_AUTH_FAILED"


async def test_transient_failure_recovers_on_a_later_attempt(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    identifier = await queued_job(db_factory)
    calls = 0

    async def health(*_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return {"status": "passed"}

    monkeypatch.setattr(worker, "health", health)
    assert await worker.run_job()
    async with db_factory() as db:
        job = await db.get(Job, identifier)
        assert job.status == "PENDING"
        assert job.error_category == "TRANSIENT_CONNECTIVITY"
        job.next_attempt_at = now() - timedelta(seconds=1)
        await db.commit()
    assert await worker.run_job()
    async with db_factory() as db:
        job = await db.get(Job, identifier)
        assert job.status == "COMPLETED" and job.attempt_count == 2
        assert job.result_json == {"status": "passed"}
        assert job.error is None and len(job.failure_history_json) == 1


def test_failure_classification_and_bounded_jitter(monkeypatch):
    assert classify_failure(TimeoutError()).category == "TRANSIENT_CONNECTIVITY"
    schema = classify_failure(DomainError("DESTINATION_SCHEMA_ERROR", "incompatible"))
    assert schema.category == "SCHEMA_INCOMPATIBILITY" and schema.retryable is False
    history = classify_failure(DomainError("CDC_WAL_ERROR", "missing WAL"))
    assert history.category == "MISSING_SOURCE_HISTORY" and history.retryable is False
    resource = classify_failure(DomainError("RESOURCE_EXHAUSTED", "busy", 503))
    assert resource.category == "RESOURCE_EXHAUSTION" and resource.retryable is True
    monkeypatch.setattr("app.workers.retry.random.uniform", lambda low, high: high)
    assert retry_delay_seconds(1, 2, 60, 0.25) == 2.5
    assert retry_delay_seconds(10, 2, 60, 0.25) == 60


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


async def test_reconcile_failure_is_isolated_per_resource(db_factory, monkeypatch):
    monkeypatch.setattr(worker, "Session", db_factory)
    pipeline_ids = [uuid4(), uuid4()]
    delivery_ids = [uuid4(), uuid4()]
    async with db_factory() as db:
        source = Source(
            name="reconcile-source",
            type="postgresql",
            host="source",
            port=5432,
            database_name="commerce",
            username="cdc",
        )
        kafka = KafkaCluster(name="reconcile-kafka", bootstrap_servers="kafka:9092")
        db.add_all([source, kafka])
        await db.flush()
        connect = ConnectCluster(
            name="reconcile-connect",
            base_url="http://connect:8083",
            kafka_cluster_id=kafka.id,
        )
        db.add(connect)
        await db.flush()
        connectors = [
            Connector(
                name=f"connector-{index}",
                connect_cluster_id=connect.id,
                connector_class="test.Connector",
                config_json={},
            )
            for index in range(4)
        ]
        db.add_all(connectors)
        await db.flush()
        db.add_all(
            Pipeline(
                id=identifier,
                name=f"pipeline-{index}",
                source_id=source.id,
                kafka_cluster_id=kafka.id,
                connect_cluster_id=connect.id,
                connector_id=connectors[index].id,
                topic_prefix=f"topic-{index}",
                snapshot_mode="initial",
            )
            for index, identifier in enumerate(pipeline_ids)
        )
        await db.flush()
        db.add_all(
            PipelineDestination(
                id=identifier,
                pipeline_id=pipeline_ids[index],
                destination_id=source.id,
                connector_id=connectors[index + 2].id,
                name=f"delivery-{index}",
            )
            for index, identifier in enumerate(delivery_ids)
        )
        await db.commit()

    observed: list[tuple[str, object]] = []

    async def reconcile_pipeline(session, pipeline):
        observed.append(("pipeline", pipeline.id))
        if pipeline.id == pipeline_ids[0]:
            raise RuntimeError("isolated pipeline failure")
        return {}

    async def reconcile_delivery(session, delivery):
        observed.append(("delivery", delivery.id))
        if delivery.id == delivery_ids[0]:
            raise RuntimeError("isolated delivery failure")
        return {}

    async def sync(session):
        observed.append(("snapshots", None))

    monkeypatch.setattr(worker, "reconcile", reconcile_pipeline)
    monkeypatch.setattr(worker, "reconcile_delivery", reconcile_delivery)
    monkeypatch.setattr(worker, "sync_snapshot_operations", sync)

    await worker._reconcile_batch()

    assert {value for kind, value in observed if kind == "pipeline"} == set(pipeline_ids)
    assert {value for kind, value in observed if kind == "delivery"} == set(delivery_ids)
    assert ("snapshots", None) in observed
