import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import ValidationError
from sqlalchemy import func, select

from app.alerts.observability import observe
from app.core.config import get_settings
from app.models.entities import (
    Alert,
    AlertRule,
    PipelineMetric,
    PipelineTable,
    now,
)
from app.models.entities import (
    ObservabilityCollectionState as State,
)
from app.schemas.observability import DeliveryObservation, Metric, Observation, SnapshotObservation
from app.services import observability as service
from app.services.observability_analysis import analyse, confirmation
from app.services.observability_providers import (
    Exporters,
    delivery_total,
    measured,
    metric,
    partition_evidence,
    sample_backlog,
)
from app.services.pipeline import _apply_initial_snapshot_notifications
from app.workers import observability as scheduler
from tests.test_phase1_lifecycle import _metadata
from tests.test_worker_leases import postgres_factory  # noqa: F401


@pytest.fixture(autouse=True)
def clear_exporter_backoff():
    from app.services.observability_providers import EXPORTER_BACKOFF, PROVIDER_BACKOFF

    EXPORTER_BACKOFF.clear()
    PROVIDER_BACKOFF.clear()
    yield
    EXPORTER_BACKOFF.clear()
    PROVIDER_BACKOFF.clear()


def sample(at=None, pipeline_id=None, delivery_id=None):
    at = at or now()
    return Observation(
        pipeline_id=pipeline_id or uuid4(),
        name="orders",
        source_provider="postgresql",
        operational_state="RUNNING",
        capture_expected_running=True,
        connector_state="RUNNING",
        collected_at=at,
        status="AVAILABLE",
        capture_lag=measured(20, "milliseconds", "debezium_jmx", at),
        capture_events=measured(100, "events", "debezium_jmx", at),
        deliveries=[
            DeliveryObservation(
                delivery_id=delivery_id or uuid4(),
                name="warehouse",
                expected_running=True,
                connector_state="RUNNING",
                group_id="real-group",
                group_state="STABLE",
                required_partitions=1,
                valid_partitions=1,
                partitions=[partition_evidence("orders", 0, 0, 40, 10, at)],
                backlog=measured(30, "records", "kafka_admin", at),
                destination_progress=metric(
                    "batches", "destination", "UNSUPPORTED", status="UNSUPPORTED"
                ),
            )
        ],
    )


@pytest.mark.parametrize(
    "low,end,committed,status,value",
    [
        (0, 20, 10, "AVAILABLE", 10),
        (10, 20, 20, "AVAILABLE", 0),
        (0, 20, None, "UNAVAILABLE", None),
        (0, 20, -1001, "UNAVAILABLE", None),
        (11, 20, 10, "INCONCLUSIVE", None),
        (0, 9, 10, "INCONCLUSIVE", None),
        (None, 20, 10, "UNAVAILABLE", None),
        (0, None, 10, "UNAVAILABLE", None),
    ],
)
def test_partition_offset_contract(low, end, committed, status, value):
    result = partition_evidence("orders", 0, low, end, committed, now())
    assert result.backlog.status == status and result.backlog.value == value


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_measurement_never_available(value):
    assert measured(value, "records", "test", now()).value is None


def test_missing_metric_cannot_have_numeric_value():
    with pytest.raises(ValidationError):
        Metric(value=0, unit="ms", source="test", reason_code="MISSING", collected_at=now())


def test_partial_coverage_and_rebalance_never_sum_to_complete_total():
    d = sample().deliveries[0]
    d.required_partitions = 2
    delivery_total(d)
    assert d.backlog.value is None and d.valid_partitions == 1
    d.required_partitions = 1
    d.group_state = "PREPARING_REBALANCE"
    delivery_total(d)
    assert d.backlog.value is None
    d.group_state = "STABLE"
    delivery_total(d)
    assert d.backlog.value == 30


@pytest.mark.parametrize(
    "count,spacing,interval,expected",
    [
        (2, 300, 300, False),
        (3, 100, 60, False),
        (6, 60, 60, True),
        (3, 150, 150, True),
        (3, 300, 300, True),
        (6, 200, 60, False),
    ],
)
def test_sink_stall_requires_window_and_consecutive_fresh_samples(
    count, spacing, interval, expected, monkeypatch
):
    monkeypatch.setattr(get_settings(), "observability_interval_seconds", interval)
    p, d = uuid4(), uuid4()
    points = [
        sample(now() - timedelta(seconds=(count - i - 1) * spacing), p, d) for i in range(count)
    ]
    findings = analyse(points[-1], points[:-1])
    assert any(f.code == "SINK_PROGRESS_STALLED" for f in findings) is expected


@pytest.mark.parametrize("cause", ["paused", "missing", "zero", "reset", "group_change"])
def test_stall_invalidating_evidence(cause):
    p, d = uuid4(), uuid4()
    points = [sample(now() - timedelta(seconds=(5 - i) * 60), p, d) for i in range(6)]
    target = points[3].deliveries[0]
    if cause == "paused":
        target.expected_running = False
    elif cause == "missing":
        target.backlog = metric("records", "test", "MISSING")
    elif cause == "zero":
        target.backlog.value = 0
    elif cause == "reset":
        target.partitions[0].committed_offset = 5
    else:
        target.group_id = "another-group"
    assert not any(f.code == "SINK_PROGRESS_STALLED" for f in analyse(points[-1], points[:-1]))


def test_capture_delay_requires_connector_event_activity():
    before = sample(now() - timedelta(seconds=60))
    after = before.model_copy(deep=True)
    after.collected_at = now()
    after.capture_lag.value = 400000
    assert not any(f.code == "CAPTURE_DELAY_SUSPECTED" for f in analyse(after, [before]))
    after.capture_events.value = 101
    assert any(f.code == "CAPTURE_DELAY_SUSPECTED" for f in analyse(after, [before]))


def test_telemetry_loss_breaks_confirmation_but_never_recovers():
    state = {}
    at = now()
    assert confirmation(state, "lag", True, at) is None
    assert confirmation(state, "lag", True, at + timedelta(seconds=60)) is None
    assert confirmation(state, "lag", True, at + timedelta(seconds=120)) == "firing"
    assert confirmation(state, "lag", None, at + timedelta(seconds=180)) is None
    assert confirmation(state, "lag", False, at + timedelta(seconds=240)) is None
    assert confirmation(state, "lag", False, at + timedelta(seconds=300)) == "resolved"


def test_stale_response_nulls_values_without_rewriting_history():
    point = sample(now() - timedelta(seconds=200))
    result = service.fresh_view(point)
    assert result.status == "STALE" and result.capture_lag.value is None
    assert point.capture_lag.value == 20


@respx.mock
async def test_exporter_requires_verified_worker_and_unique_series():
    respx.get("http://trusted:9404/metrics").mock(
        return_value=httpx.Response(
            200,
            text="""
jmx_scrape_error 0
cluecdc_capture_lag_ms{provider="postgres",context="streaming",server="orders"} 25
""",
        )
    )
    e = Exporters()
    await e.scrape({"worker:8083": "http://trusted:9404/metrics"})
    tasks = [{"id": 0, "state": "RUNNING", "worker_id": "worker:8083"}]
    assert e.capture(tasks, "orders", "postgres", "cluecdc_capture_lag_ms", "ms").value == 25
    tasks[0]["worker_id"] = "other:8083"
    assert e.capture(tasks, "orders", "postgres", "cluecdc_capture_lag_ms", "ms").value is None
    assert e.capture(tasks * 2, "orders", "postgres", "cluecdc_capture_lag_ms", "ms").value is None


@respx.mock
async def test_exporter_errors_and_redirects_are_not_healthy():
    respx.get("http://trusted:9404/metrics").mock(
        return_value=httpx.Response(302, headers={"Location": "http://untrusted/metrics"})
    )
    e = Exporters()
    await e.scrape({"worker": "http://trusted:9404/metrics"})
    assert not e.samples


def test_sink_metrics_require_all_tasks_and_workers():
    e = Exporters()
    assert all(
        m.value is None
        for m in e.sink([{"id": 0, "state": "RUNNING", "worker_id": "missing"}], "sink").values()
    )


def test_sink_aggregation_requires_exact_multiworker_task_coverage():
    e = Exporters()
    for worker, task, value in [("worker-a", 0, 3), ("worker-b", 1, 7)]:
        e.scraped_at[worker] = now()
        e.samples[worker] = [
            SimpleNamespace(
                name="cluecdc_sink_" + name,
                labels={"connector": "sink", "task": str(task)},
                value=value,
            )
            for name in [
                "active_count",
                "lag_max",
                "read_rate",
                "send_rate",
                "offset_commit_completion_rate",
            ]
        ]
    tasks = [
        {"id": 0, "worker_id": "worker-a", "state": "RUNNING"},
        {"id": 1, "worker_id": "worker-b", "state": "RUNNING"},
    ]
    result = e.sink(tasks, "sink")
    assert result["active_count"].value == 10
    assert result["lag_max"].value == 7
    assert result["read_rate"].value == 10
    e.samples.pop("worker-b")
    assert all(m.value is None for m in e.sink(tasks, "sink").values())


def test_sink_stale_zero_is_not_published_as_measured_zero():
    e = Exporters()
    e.scraped_at["worker"] = now()
    e.samples["worker"] = [
        SimpleNamespace(
            name="cluecdc_sink_active_count",
            labels={"connector": "sink", "task": "0"},
            value=0,
            timestamp=0,
        )
    ]
    m = e.sink([{"id": 0, "worker_id": "worker", "state": "RUNNING"}], "sink")["active_count"]
    assert m.status == "STALE" and m.value is None


def test_initial_snapshot_replay_does_not_refresh_progress_or_regress_completion():
    table = PipelineTable(snapshot_status="RUNNING", schema_name="public", table_name="orders")
    record = {"aggregate_type": "Initial Snapshot", "type": "IN_PROGRESS"}
    _apply_initial_snapshot_notifications([table], [record])
    at = table.snapshot_last_activity_at
    _apply_initial_snapshot_notifications([table], [record])
    assert table.snapshot_last_activity_at == at
    table.snapshot_status = "COMPLETED"
    _apply_initial_snapshot_notifications([table], [{**record, "type": "STARTED"}])
    assert table.snapshot_status == "COMPLETED"


async def test_cached_api_history_capabilities_and_redaction(client, db_factory):
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        await db.commit()
        identifier = pipeline.id
    result = await client.get(f"/api/v1/pipelines/{identifier}/observability")
    assert result.status_code == 200
    assert result.json()["capture_lag"]["value"] is None
    assert "encrypted" not in result.text and "password" not in result.text
    assert (
        await client.get(f"/api/v1/pipelines/{identifier}/observability/capabilities")
    ).status_code == 200
    history = await client.get(f"/api/v1/pipelines/{identifier}/observability/history")
    assert history.status_code == 200 and history.json()["gaps"]
    invalid = await client.get(
        f"/api/v1/pipelines/{identifier}/observability/history",
        params={"start": (now() - timedelta(days=8)).isoformat()},
    )
    assert invalid.status_code == 422
    assert (await client.get("/api/v1/observability/summary")).status_code == 200


async def test_lease_fencing_expiry_and_idempotent_bucket(db_factory):
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        await db.commit()
        await scheduler.seed(db)
        await db.commit()
        identifier, bucket = await scheduler.claim(db, "first")
        assert await scheduler.claim(db, "second") is None
        point = sample(pipeline_id=identifier)
        assert not await scheduler.finish(db, identifier, bucket, "second", point, 1, False)
        state = await db.get(State, identifier)
        state.lease_expires_at = now() - timedelta(seconds=1)
        await db.commit()
        assert not await scheduler.finish(db, identifier, bucket, "first", point, 1, False)
        assert (await scheduler.claim(db, "second"))[0] == identifier
        assert await scheduler.finish(db, identifier, bucket, "second", point, 1, False)
        state.next_due = now() - timedelta(seconds=1)
        await db.commit()
        await scheduler.claim(db, "third")
        assert await scheduler.finish(db, identifier, bucket, "third", point, 1, False)
        assert await db.scalar(select(func.count()).select_from(PipelineMetric)) == 1


async def test_history_bounds_gaps_legacy_and_retention(db_factory):
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        for i in range(505):
            at = now() - timedelta(seconds=600 - i)
            point = sample(at, pipeline.id)
            db.add(
                PipelineMetric(
                    pipeline_id=pipeline.id,
                    sample_bucket=at,
                    created_at=at,
                    values_json=point.model_dump(mode="json"),
                )
            )
        old = now() - timedelta(days=8)
        db.add_all(
            [
                PipelineMetric(
                    pipeline_id=pipeline.id, created_at=old, values_json={"legacy": True}
                ),
                PipelineMetric(
                    pipeline_id=pipeline.id, created_at=old, sample_bucket=old, values_json={}
                ),
            ]
        )
        await db.commit()
        result = await service.history(db, pipeline.id, now() - timedelta(hours=1), now())
        assert len(result.points) == 500 and result.truncated and result.gaps
        await service.cleanup(db)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(PipelineMetric)) == 506


async def test_explicit_alert_rule_confirmation_missing_and_recovery(db_factory):
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        rule = AlertRule(name="capture", event_types=["CDC_CAPTURE_LAG_HIGH"])
        state = State(pipeline_id=pipeline.id, next_due=now(), latest_json={}, analysis_json={})
        db.add_all([rule, state])
        await db.flush()
        point = sample(pipeline_id=pipeline.id)
        point.operational_state = (
            "DEGRADED"  # An independent sink failure must not hide capture lag.
        )
        point.capture_lag.value = 400001
        for _ in range(3):
            point.collected_at += timedelta(seconds=60)
            await observe(db, point, state)
        alert = await db.scalar(select(Alert))
        assert alert.status == "firing"
        assert alert.details["evidence"]["capture_lag"]["value"] == 400001
        point.capture_lag = metric("milliseconds", "test", "MISSING")
        point.collected_at += timedelta(seconds=60)
        await observe(db, point, state)
        assert alert.status == "firing"
        point.capture_lag = measured(1, "milliseconds", "test", now())
        point.collected_at += timedelta(seconds=60)
        await observe(db, point, state)
        assert alert.status == "firing"
        point.collected_at += timedelta(seconds=60)
        await observe(db, point, state)
        assert alert.status == "resolved"
        assert alert.details["evidence"]["capture_lag"]["value"] == 1


async def test_two_postgres_collectors_claim_once(postgres_factory, monkeypatch):  # noqa: F811
    # Scheduling and leases must use one database clock despite worker clock skew.
    worker_time = now() + timedelta(hours=1)
    monkeypatch.setattr(scheduler, "now", lambda: worker_time)
    async with postgres_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        await db.commit()
        await scheduler.seed(db)
        await db.commit()

    async def claim(owner):
        async with postgres_factory() as db:
            return await scheduler.claim(db, owner)

    claims = await asyncio.gather(claim("one"), claim("two"))
    assert sum(c is not None for c in claims) == 1


async def test_postgres_bucket_fencing_expiry_and_idempotency(postgres_factory):  # noqa: F811
    await test_lease_fencing_expiry_and_idempotent_bucket(postgres_factory)


async def test_postgres_global_collection_ceiling_across_workers(postgres_factory):  # noqa: F811
    from app.models.entities import Pipeline

    async with postgres_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        for i in range(5):
            db.add(
                Pipeline(
                    name=f"bounded-{i}",
                    source_id=pipeline.source_id,
                    kafka_cluster_id=pipeline.kafka_cluster_id,
                    connect_cluster_id=pipeline.connect_cluster_id,
                    topic_prefix=f"bounded_{i}",
                    snapshot_mode="initial",
                )
            )
        await db.commit()
        await scheduler.seed(db)
        await db.commit()

    async def claim(owner):
        async with postgres_factory() as db:
            return await scheduler.claim(db, owner)

    claimed = await asyncio.gather(*(claim(f"worker-{i}") for i in range(8)))
    assert sum(c is not None for c in claimed) == 4
    assert len({c[0] for c in claimed if c}) == 4


async def test_completed_snapshot_preserves_final_rows_and_rejects_negative_estimate(db_factory):
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        at = now() - timedelta(days=1)
        db.add(
            PipelineTable(
                pipeline_id=pipeline.id,
                schema_name="public",
                table_name="orders",
                topic_name="orders",
                snapshot_status="COMPLETED",
                snapshot_rows_processed=42,
                snapshot_estimated_rows=-1,
                snapshot_last_activity_at=at,
                snapshot_finished_at=at,
            )
        )
        await db.flush()
        observed = await service.collect(db, pipeline)
        result = service.fresh_view(observed)
        assert result.snapshots[0].state == "COMPLETED"
        assert result.snapshots[0].processed_rows.value == 42
        assert result.snapshots[0].estimated_rows is None


async def test_runtime_provider_failure_backs_off_without_discarding_metadata(
    db_factory, monkeypatch
):
    from app.core.errors import DomainError
    from app.models.entities import Connector
    from app.services.observability_providers import PROVIDER_BACKOFF

    calls = 0

    async def unavailable(self, name):
        nonlocal calls
        calls += 1
        raise DomainError("CONNECT_UNAVAILABLE", "Runtime unavailable", 503)

    monkeypatch.setattr("app.adapters.connect.KafkaConnectClient.status", unavailable)
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        connector = Connector(
            name="capture",
            connector_type="source",
            connect_cluster_id=pipeline.connect_cluster_id,
            connector_class="postgres",
            config_json={"topic.prefix": pipeline.topic_prefix},
        )
        db.add(connector)
        await db.flush()
        pipeline.connector_id = connector.id
        await db.commit()
        for _ in range(2):
            point = await service.collect(db, pipeline)
            assert point.capture_lag.value is None and point.connector_state == "UNKNOWN"
        assert calls == 1 and ("connect", str(connector.id)) in PROVIDER_BACKOFF


def test_candidate_group_convention_alone_never_establishes_identity(monkeypatch):
    class Future:
        def result(self, timeout):
            return SimpleNamespace(state="STABLE", members=[SimpleNamespace(client_id="unrelated")])

    class Admin:
        def __init__(self, config):
            pass

        def describe_consumer_groups(self, groups, **kwargs):
            return {groups[0]: Future()}

    monkeypatch.setattr("app.services.observability_providers.AdminClient", Admin)
    d = sample().deliveries[0]
    cluster = SimpleNamespace(bootstrap_servers="kafka:9092", security_protocol="PLAINTEXT")
    sample_backlog(cluster, {}, {"topics": "orders"}, "sink", d)
    assert d.backlog.status == "INCONCLUSIVE" and d.backlog.value is None


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("valid", True),
        ("paused", False),
        ("unknown", False),
        ("advancing", False),
        ("stale_gap", False),
    ],
)
def test_snapshot_stall_requires_fresh_unpaused_metrics(condition, expected):
    p, t = uuid4(), uuid4()
    started = now() - timedelta(minutes=20)
    points = []
    for i in range(16):
        at = now() - timedelta(seconds=(15 - i) * 60)
        point = sample(at, p)
        point.snapshots = [
            SnapshotObservation(
                table_id=t,
                table="public.orders",
                kind="INITIAL",
                state="RUNNING",
                started_at=started,
                last_progress_at=started,
                processed_rows=metric("rows", "test", "MISSING"),
                provenance="debezium_notification",
                stale=True,
                running=measured(1, "boolean", "jmx", at),
                paused=measured(1 if condition == "paused" else 0, "boolean", "jmx", at),
                progress_counter=measured(
                    i if condition == "advancing" else 5, "events", "jmx", at
                ),
            )
        ]
        if condition == "unknown":
            point.snapshots[0].running = None
        if condition == "stale_gap" and i == 14:
            point.collected_at -= timedelta(seconds=200)
        points.append(point)
    findings = analyse(points[-1], points[:-1])
    assert any(f.code == "SNAPSHOT_PROGRESS_STALLED" for f in findings) is expected


@respx.mock
async def test_failed_exporter_backoff_avoids_repeated_requests():
    request = respx.get("http://trusted:9404/metrics").mock(return_value=httpx.Response(503))
    await Exporters().scrape({"worker": "http://trusted:9404/metrics"})
    e = Exporters()
    await e.scrape({"worker": "http://trusted:9404/metrics"})
    assert request.call_count == 1 and e.errors["worker"] == "EXPORTER_BACKOFF"


@respx.mock
async def test_old_prometheus_timestamp_is_stale_not_zero():
    respx.get("http://trusted:9404/metrics").mock(
        return_value=httpx.Response(
            200,
            text="""
jmx_scrape_error 0
cluecdc_capture_lag_ms{provider="postgres",context="streaming",server="orders"} 0 1000000000000
""",
        )
    )
    e = Exporters()
    await e.scrape({"worker": "http://trusted:9404/metrics"})
    m = e.capture(
        [{"id": 0, "state": "RUNNING", "worker_id": "worker"}],
        "orders",
        "postgres",
        "cluecdc_capture_lag_ms",
        "milliseconds",
    )
    assert m.status == "STALE" and m.value is None


@pytest.mark.parametrize("role", ["Viewer", "Ops", "Admin"])
async def test_observability_rbac_cached_read_paths(client, db_factory, role, monkeypatch):
    from app.core.auth import Principal, principal
    from app.main import app

    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        await db.commit()
        identifier = pipeline.id

    def forbidden_live_sampling(*args, **kwargs):
        raise AssertionError("GET endpoints must not sample")

    monkeypatch.setattr(service, "collect", forbidden_live_sampling)
    app.dependency_overrides[principal] = lambda: Principal(actor="reader", role=role)
    for path in [
        "/observability/summary",
        f"/pipelines/{identifier}/observability",
        f"/pipelines/{identifier}/observability/history",
        f"/pipelines/{identifier}/observability/capabilities",
    ]:
        assert (await client.get("/api/v1" + path)).status_code == 200
    if role != "Admin":
        assert (
            await client.post("/api/v1/alert-rules", json={"name": "forbidden"})
        ).status_code == 403


async def test_observability_requires_authentication(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_mode", "token")
    assert (await client.get("/api/v1/observability/summary")).status_code == 401


async def test_legacy_alert_rule_update_preserves_threshold_configuration(client):
    created = await client.post(
        "/api/v1/alert-rules",
        json={
            "name": "configured",
            "event_types": ["CDC_CAPTURE_LAG_HIGH"],
            "config_json": {"observability": {"capture_lag_ms": 42}},
        },
    )
    assert created.status_code == 201
    identifier = created.json()["id"]
    changed = await client.put(
        f"/api/v1/alert-rules/{identifier}",
        json={"name": "renamed", "event_types": ["CDC_CAPTURE_LAG_HIGH"]},
    )
    assert changed.status_code == 200
    assert changed.json()["config_json"]["observability"]["capture_lag_ms"] == 42


async def test_whole_collection_timeout_preserves_partial_evidence_and_no_jobs(
    db_factory, monkeypatch
):
    from app.models.entities import AuditLog, Job

    async with db_factory() as db:
        await _metadata(db)
        await db.commit()
        await scheduler.seed(db)
        await db.commit()

    async def slow(session, pipeline, *, result):
        result.capture_lag = measured(42, "milliseconds", "debezium_jmx", now())
        await asyncio.Event().wait()

    monkeypatch.setattr(scheduler, "Session", db_factory)
    monkeypatch.setattr(service, "collect", slow)
    monkeypatch.setattr(get_settings(), "observability_budget_seconds", 0.01)
    assert await scheduler.run_once()
    async with db_factory() as db:
        state = await db.scalar(select(State))
        assert state.latest_json["capture_lag"]["value"] == 42
        assert state.latest_json["status"] == "PARTIAL" and state.last_success_at is None
        assert state.failures == 1
        assert await db.scalar(select(func.count()).select_from(Job)) == 0
        assert await db.scalar(select(func.count()).select_from(AuditLog)) == 0
