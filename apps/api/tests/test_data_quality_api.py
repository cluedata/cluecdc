from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.entities import (
    Alert,
    AuditLog,
    Connection,
    DataQualityCheck,
    DataQualityRuleResult,
    DataQualityRun,
    Job,
    NotificationChannel,
    NotificationDelivery,
    User,
    now,
)
from app.schemas.data_quality import CheckInput
from app.services import data_quality as service
from app.services import data_quality_engine as engine
from app.services.data_quality_alerts import observe
from app.services.secrets import secret_provider
from app.workers import worker


@pytest.fixture
async def draft(db_factory, monkeypatch):
    async with db_factory() as db:
        source = Connection(
            name="dq-source", type="postgresql", database_name="dq", host="test", port=5432
        )
        destination = Connection(
            name="dq-dest", type="postgresql", database_name="dq", host="test", port=5432
        )
        db.add_all([source, destination])
        await db.commit()
        ids = [str(source.id), str(destination.id)]

    async def valid(*args, **kwargs):
        return {}, {}, {"status": "SUPPORTED", "rules": [], "warnings": []}

    monkeypatch.setattr(engine, "inspect", valid)
    return {
        "name": "independent check",
        "source": {
            "connection_id": ids[0],
            "database": "dq",
            "schema_name": "public",
            "table": "items",
            "current_state_confirmed": True,
        },
        "destination": {
            "connection_id": ids[1],
            "database": "dq",
            "schema_name": "public",
            "table": "items",
            "current_state_confirmed": True,
        },
        "rules": [{"type": "row_count"}, {"type": "row_count_diff", "tolerance": 2}],
    }


async def create(client, draft):
    response = await client.post("/api/v1/data-quality/checks", json=draft)
    assert response.status_code == 201, response.text
    return response.json()


async def test_crud_run_idempotency_history_and_audit(client, db_factory, draft, monkeypatch):
    item = await create(client, draft)
    path = f"/api/v1/data-quality/checks/{item['id']}"
    assert item["configuration"]["schedule"]["frequency_seconds"] == 600
    assert not item["alert_status"]["configured"]
    run = (await client.post(path + "/run", json={"request_key": "manual-1"})).json()
    repeat = await client.post(path + "/run", json={"request_key": "manual-1"})
    assert repeat.json()["id"] == run["id"]
    assert (await client.post(path + "/run")).status_code == 409

    async def measurements(session, config, timestamp):
        return (
            [{"rule_id": str(r.id), "rule_type": r.type, "quality": "PASS"} for r in config.rules],
            {},
        )

    monkeypatch.setattr(engine, "execute", measurements)
    monkeypatch.setattr(worker, "Session", db_factory)
    assert await worker.run_job()
    detail = (await client.get(f"/api/v1/data-quality/runs/{run['id']}")).json()
    assert detail["quality"] == "PASS" and detail["status"] == "COMPLETED"
    assert len(detail["results"]) == 2
    patch = await client.patch(path, json={"name": "edited", "rules": [{"type": "row_count"}]})
    assert patch.status_code == 200, patch.text
    assert len((await client.get(f"/api/v1/data-quality/runs/{run['id']}/results")).json()) == 2
    assert (await client.get(path + "/runs?limit=1")).json()["items"][0]["id"] == run["id"]
    assert (await client.get("/api/v1/data-quality/checks?rule=row_count&quality=PASS")).json()[
        "items"
    ]
    assert (await client.delete(path)).status_code == 200
    assert (await client.get(path)).status_code == 404
    async with db_factory() as db:
        actions = set((await db.scalars(select(AuditLog.action))).all())
        assert {
            "data_quality.created",
            "data_quality.queued",
            "data_quality.finished",
            "data_quality.updated",
            "data_quality.deleted",
        } <= actions


async def test_scheduler_due_overlap_pause_resume_and_immutable_snapshot(client, db_factory, draft):
    draft["schedule"] = {"enabled": True}
    item = await create(client, draft)
    identifier = UUID(item["id"])
    async with db_factory() as db:
        check = await db.get(DataQualityCheck, identifier)
        clock = now() + timedelta(seconds=601)
        assert await service.schedule_due(db, clock) == 1
        await db.commit()
        assert await service.schedule_due(db, clock) == 0
        assert await service.schedule_due(db, clock + timedelta(seconds=601)) == 0
        await db.commit()
        run = await db.scalar(select(DataQualityRun))
        assert run.snapshot_json["name"] == draft["name"]
        assert await db.scalar(select(func.count()).select_from(Job)) == 1
    path = f"/api/v1/data-quality/checks/{identifier}"
    assert (await client.post(path + "/pause")).json()["next_run_at"] is None
    resumed = (await client.post(path + "/resume")).json()
    assert resumed["next_run_at"] and resumed["configuration"]["schedule"]["enabled"]
    async with db_factory() as db:
        job = await db.scalar(select(Job))
        job.status = "FAILED"
        await db.commit()
        await service.schedule_due(db)
        await db.commit()
        check = await db.get(DataQualityCheck, identifier)
        assert check.active_run_id is None
        assert (await db.scalar(select(DataQualityRun))).quality == "ERROR"


async def test_worker_errors_are_sanitized_no_scan_retry(client, db_factory, draft, monkeypatch):
    item = await create(client, draft)
    run = (await client.post(f"/api/v1/data-quality/checks/{item['id']}/run")).json()

    async def fail(*args):
        raise RuntimeError("password=do-not-leak raw row 123")

    monkeypatch.setattr(engine, "execute", fail)
    monkeypatch.setattr(worker, "Session", db_factory)
    await worker.run_job()
    response = await client.get(f"/api/v1/data-quality/runs/{run['id']}")
    assert "do-not-leak" not in response.text
    assert response.json()["status"] == "FAILED"
    async with db_factory() as db:
        job = await db.get(Job, UUID(run["job_id"]))
        assert job.status == "COMPLETED" and job.attempt_count == 1


async def test_alert_thresholds_recovery_error_separation_and_dedup(
    client, db_factory, draft, monkeypatch
):
    async with db_factory() as db:
        ref = await secret_provider(db).put_secret({"url": "https://example.invalid"})
        channel = NotificationChannel(
            name="dq controlled transport", type="webhook", config_encrypted=ref
        )
        db.add(channel)
        await db.commit()
        draft["alerting"] = {"channel_id": str(channel.id)}
    item = await create(client, draft)
    assert item["configuration"]["alerting"]["enabled"] is True
    async with db_factory() as db:
        check = await db.get(DataQualityCheck, UUID(item["id"]))
        config = CheckInput.model_validate(check.config_json)
        rule = config.rules[0]
        run = await service.enqueue(db, check, "test")
        await db.flush()

        async def observation(status, new_run=True):
            if new_run:
                # Unique occurrence identity models independent completed runs.
                run.id = uuid4()
            result = [{"rule_id": str(rule.id), "rule_type": rule.type, "quality": status}]
            await observe(db, check, run, result, config)
            await db.flush()

        await observation("FAIL", False)
        assert await db.scalar(select(func.count()).select_from(Alert)) == 0
        await observation("FAIL")
        assert await db.scalar(select(func.count()).select_from(Alert)) == 1
        assert await db.scalar(select(func.count()).select_from(NotificationDelivery)) == 1
        await observation("FAIL", False)
        assert await db.scalar(select(func.count()).select_from(NotificationDelivery)) == 1
        await observation("INCONCLUSIVE")
        assert (await db.scalar(select(Alert))).status == "firing"
        await observation("ERROR")
        assert await db.scalar(select(func.count()).select_from(Alert)) == 2
        await observation("PASS")
        assert all(a.status == "resolved" for a in (await db.scalars(select(Alert))).all())
        assert await db.scalar(select(func.count()).select_from(NotificationDelivery)) == 4
        await db.commit()
        from app.alerts.dispatcher import dispatch_pending
        from app.alerts.domain import NotificationResult

        sent = []

        class Transport:
            async def send(self, payload, config):
                sent.append(payload.status)
                return NotificationResult(success=True)

        monkeypatch.setattr("app.alerts.dispatcher.provider_for", lambda kind: Transport())
        assert await dispatch_pending(db) == 4
        assert sorted(sent) == ["firing", "firing", "resolved", "resolved"]
        await observation("FAIL")
        await observation("FAIL")
        # A new incident during cooldown cannot enqueue another notification.
        assert await db.scalar(select(func.count()).select_from(NotificationDelivery)) == 4


async def test_channel_unavailable_and_lease_loss_do_not_claim_success(
    client, db_factory, draft, monkeypatch
):
    item = await create(client, draft)
    path = f"/api/v1/data-quality/checks/{item['id']}"
    run = (await client.post(path + "/run")).json()
    stolen = False

    async def measurements(session, config, timestamp):
        nonlocal stolen
        if not stolen:
            async with db_factory() as other:
                job = await other.get(Job, UUID(run["job_id"]))
                job.claimed_by = "another-worker"
                await other.commit()
            stolen = True
        return (
            [{"rule_id": str(r.id), "rule_type": r.type, "quality": "PASS"} for r in config.rules],
            {},
        )

    monkeypatch.setattr(engine, "execute", measurements)
    monkeypatch.setattr(worker, "Session", db_factory)
    await worker.run_job()
    async with db_factory() as db:
        assert await db.scalar(select(func.count()).select_from(DataQualityRuleResult)) == 0
        job = await db.get(Job, UUID(run["job_id"]))
        job.status, job.claimed_by, job.lease_expires_at = "PENDING", None, None
        await db.commit()
    await worker.run_job()
    async with db_factory() as db:
        assert await db.scalar(select(func.count()).select_from(DataQualityRuleResult)) == 2
        ref = await secret_provider(db).put_secret({"url": "https://example.invalid"})
        channel = NotificationChannel(name="channel-state", type="webhook", config_encrypted=ref)
        db.add(channel)
        await db.commit()
        channel_id = channel.id
    response = await client.patch(path, json={"alerting": {"channel_id": str(channel_id)}})
    assert response.json()["alert_status"]["configured"]
    async with db_factory() as db:
        channel = await db.get(NotificationChannel, channel_id)
        channel.enabled = False
        await db.commit()
    response = await client.get(path)
    assert response.json()["alert_status"]["configured"]
    assert not response.json()["alert_status"]["available"]
    assert (
        await client.patch(path, json={"description": "Preserve unavailable channel"})
    ).status_code == 200


async def test_retention_removes_only_expired_finished_quality_history(client, db_factory, draft):
    item = await create(client, draft)
    async with db_factory() as db:
        check = await db.get(DataQualityCheck, UUID(item["id"]))
        run = await service.enqueue(db, check, "retention-test")
        run.status, run.quality = "COMPLETED", "PASS"
        run.finished_at = now() - timedelta(days=91)
        check.active_run_id = None
        db.add(
            DataQualityRuleResult(
                run_id=run.id,
                rule_id=uuid4(),
                rule_type="row_count",
                quality="PASS",
                evidence_json={"consistency": "BEST_EFFORT"},
            )
        )
        await db.commit()
        await service.cleanup(db)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(DataQualityRun)) == 0
        assert await db.scalar(select(func.count()).select_from(DataQualityRuleResult)) == 0
        assert await db.scalar(select(func.count()).select_from(Job)) == 1
        assert await db.scalar(select(func.count()).select_from(AuditLog)) > 0


@pytest.mark.parametrize(
    "role,write,run,delete",
    [("ADMIN", 201, 202, 200), ("OPS", 201, 202, 403), ("VIEWER", 403, 403, 403)],
)
async def test_real_session_rbac(client, db_factory, draft, monkeypatch, role, write, run, delete):
    item = await create(client, draft)
    monkeypatch.setattr(get_settings(), "auth_mode", "session")
    email = f"{role.lower()}@dq.test"
    async with db_factory() as db:
        db.add(
            User(
                email=email,
                role=role,
                status="ACTIVE",
                password_hash=PasswordHasher().hash("dq session password"),
            )
        )
        await db.commit()
    assert (await client.get("/api/v1/data-quality/checks")).status_code == 401
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "dq session password"}
    )
    assert login.status_code == 200
    assert (await client.get("/api/v1/data-quality/checks")).status_code == 200
    assert (await client.get("/api/v1/data-quality/rules/catalog")).status_code == 200
    assert (await client.post("/api/v1/data-quality/checks", json=draft)).status_code == write
    path = f"/api/v1/data-quality/checks/{item['id']}"
    assert (await client.post(path + "/run")).status_code == run
    assert (await client.delete(path)).status_code == delete
