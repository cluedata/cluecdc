from datetime import timedelta

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.models.entities import (
    ConnectCluster,
    Connection,
    Connector,
    KafkaCluster,
    Pipeline,
    PipelineDestination,
    SecretReference,
    now,
)
from app.services.connector_recovery import reconcile_failed_tasks


async def recovery_entities(db_factory):
    async with db_factory() as db:
        secret = SecretReference(provider="database", ciphertext="cipher")
        kafka = KafkaCluster(
            name="recovery-kafka",
            bootstrap_servers="kafka:29092",
            security_protocol="PLAINTEXT",
        )
        db.add_all([secret, kafka])
        await db.flush()
        source = Connection(
            name="source",
            category="DATABASE",
            provider="POSTGRESQL",
            capabilities_json=["SOURCE"],
            config_json={},
            secret_ref=secret.id,
        )
        target = Connection(
            name="target",
            category="DATABASE",
            provider="POSTGRESQL",
            capabilities_json=["DESTINATION"],
            config_json={},
            secret_ref=secret.id,
        )
        db.add_all([source, target])
        await db.flush()
        cluster = ConnectCluster(
            name="recovery-connect",
            base_url="http://connect:8083",
            kafka_cluster_id=kafka.id,
        )
        db.add(cluster)
        await db.flush()
        connector = Connector(
            name="sink-recovery",
            connector_type="sink",
            connect_cluster_id=cluster.id,
            connector_class="jdbc",
            config_json={},
            desired_state="RUNNING",
            actual_state="DEGRADED",
        )
        pipeline = Pipeline(
            name="recovery-pipeline",
            source_id=source.id,
            kafka_cluster_id=kafka.id,
            connect_cluster_id=cluster.id,
            topic_prefix="recovery",
            snapshot_mode="initial",
        )
        db.add_all([connector, pipeline])
        await db.flush()
        delivery = PipelineDestination(
            pipeline_id=pipeline.id,
            destination_id=target.id,
            connector_id=connector.id,
            name="recovery-delivery",
            desired_state="RUNNING",
            actual_state="DEGRADED",
        )
        db.add(delivery)
        await db.commit()
        return delivery.id, connector.id, cluster.id


def failed_status(*, retryable=True, task_ids=(0,)):
    return {
        "connector": {"state": "RUNNING"},
        "tasks": [
            {
                "id": task_id,
                "state": "FAILED",
                "error": "Destination database connection failed",
                "error_code": "DESTINATION_CONNECTION_UNAVAILABLE",
                "error_category": "TRANSIENT_CONNECTIVITY",
                "retryable": retryable,
            }
            for task_id in task_ids
        ],
    }


async def load(db, delivery_id, connector_id, cluster_id):
    return (
        await db.get(PipelineDestination, delivery_id),
        await db.get(Connector, connector_id),
        await db.get(ConnectCluster, cluster_id),
    )


async def test_transient_task_restart_is_leased_persisted_and_verified(
    db_factory, respx_mock, monkeypatch
):
    delivery_id, connector_id, cluster_id = await recovery_entities(db_factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_recovery_base_delay_seconds", 1)
    monkeypatch.setattr(settings, "auto_recovery_max_delay_seconds", 1)
    restart = respx_mock.post("http://connect:8083/connectors/sink-recovery/tasks/0/restart")
    restart.mock(return_value=httpx.Response(200, json={}))

    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        result = await reconcile_failed_tasks(
            db,
            delivery=delivery,
            connector=connector,
            cluster=cluster,
            status=failed_status(),
        )
        assert result["auto_recovery"]["status"] == "VERIFYING"
        assert result["auto_recovery"]["attempt_count"] == 1
        assert result["auto_recovery"]["history"][0]["outcome"] == "RESTART_REQUEST_ACCEPTED"
        assert connector.operation_token is None
    assert restart.call_count == 1

    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        result = await reconcile_failed_tasks(
            db,
            delivery=delivery,
            connector=connector,
            cluster=cluster,
            status={
                "connector": {"state": "RUNNING"},
                "tasks": [{"id": 0, "state": "RUNNING"}],
            },
        )
        assert result["auto_recovery"]["status"] == "RECOVERED"
        assert result["auto_recovery"]["last_cycle_attempts"] == 1
        assert result["auto_recovery"]["last_successful_recovery_at"]


async def test_recovery_respects_pause_disable_permanent_failure_and_budget(
    db_factory, respx_mock, monkeypatch
):
    delivery_id, connector_id, cluster_id = await recovery_entities(db_factory)
    restart = respx_mock.post("http://connect:8083/connectors/sink-recovery/tasks/0/restart")
    restart.mock(return_value=httpx.Response(200, json={}))
    settings = get_settings()

    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        delivery.desired_state = "PAUSED"
        result = await reconcile_failed_tasks(
            db, delivery=delivery, connector=connector, cluster=cluster, status=failed_status()
        )
        assert result["auto_recovery"]["status"] == "SUSPENDED"
        await db.rollback()

    monkeypatch.setattr(settings, "auto_recovery_enabled", False)
    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        result = await reconcile_failed_tasks(
            db, delivery=delivery, connector=connector, cluster=cluster, status=failed_status()
        )
        assert result["auto_recovery"]["status"] == "DISABLED"

    monkeypatch.setattr(settings, "auto_recovery_enabled", True)
    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        result = await reconcile_failed_tasks(
            db,
            delivery=delivery,
            connector=connector,
            cluster=cluster,
            status=failed_status(retryable=False),
        )
        assert result["auto_recovery"]["status"] == "NOT_RETRYABLE"

    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        connector.runtime_json = {
            "auto_recovery": {
                "status": "VERIFYING",
                "attempt_count": settings.auto_recovery_max_attempts,
                "first_failure_at": (now() - timedelta(minutes=1)).isoformat(),
            }
        }
        await db.commit()
        result = await reconcile_failed_tasks(
            db, delivery=delivery, connector=connector, cluster=cluster, status=failed_status()
        )
        assert result["auto_recovery"]["status"] == "EXHAUSTED"
        assert "manually" in result["auto_recovery"]["exhaustion_reason"]
    assert restart.call_count == 0


async def test_active_connector_lease_prevents_duplicate_recovery(db_factory, respx_mock):
    delivery_id, connector_id, cluster_id = await recovery_entities(db_factory)
    async with db_factory() as db:
        connector = await db.get(Connector, connector_id)
        connector.operation_token = "other-worker"
        connector.operation_name = "auto-restart-task"
        connector.operation_started_at = now()
        await db.commit()

    restart = respx_mock.post("http://connect:8083/connectors/sink-recovery/tasks/0/restart")
    restart.mock(return_value=httpx.Response(200, json={}))
    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        result = await reconcile_failed_tasks(
            db, delivery=delivery, connector=connector, cluster=cluster, status=failed_status()
        )
        assert result["auto_recovery"]["status"] == "WAITING_FOR_OPERATION"
    assert restart.call_count == 0


async def test_one_lease_restarts_every_failed_task_once(db_factory, respx_mock, monkeypatch):
    delivery_id, connector_id, cluster_id = await recovery_entities(db_factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_recovery_base_delay_seconds", 1)
    monkeypatch.setattr(settings, "auto_recovery_max_delay_seconds", 1)
    restarts = [
        respx_mock.post(f"http://connect:8083/connectors/sink-recovery/tasks/{task_id}/restart")
        for task_id in (0, 1)
    ]
    for restart in restarts:
        restart.mock(return_value=httpx.Response(200, json={}))

    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        result = await reconcile_failed_tasks(
            db,
            delivery=delivery,
            connector=connector,
            cluster=cluster,
            status=failed_status(task_ids=(0, 1)),
        )
        recovery = result["auto_recovery"]
        assert recovery["status"] == "VERIFYING"
        assert recovery["failed_task_ids"] == [0, 1]
        assert recovery["history"][0]["accepted_task_ids"] == [0, 1]
    assert [restart.call_count for restart in restarts] == [1, 1]


async def test_recovery_state_survives_a_new_session(db_factory, respx_mock, monkeypatch):
    delivery_id, connector_id, cluster_id = await recovery_entities(db_factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_recovery_base_delay_seconds", 1)
    monkeypatch.setattr(settings, "auto_recovery_max_delay_seconds", 1)
    respx_mock.post("http://connect:8083/connectors/sink-recovery/tasks/0/restart").mock(
        return_value=httpx.Response(200, json={})
    )
    async with db_factory() as db:
        delivery, connector, cluster = await load(db, delivery_id, connector_id, cluster_id)
        await reconcile_failed_tasks(
            db, delivery=delivery, connector=connector, cluster=cluster, status=failed_status()
        )
    async with db_factory() as db:
        stored = await db.scalar(select(Connector).where(Connector.id == connector_id))
        assert stored.runtime_json["auto_recovery"]["attempt_count"] == 1
        assert stored.runtime_json["auto_recovery"]["next_retry_at"]
