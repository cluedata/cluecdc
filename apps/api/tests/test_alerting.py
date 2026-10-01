from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import respx
from sqlalchemy import func, select

from app.alerts.classifier import ErrorClassifier
from app.alerts.dispatcher import dispatch_pending
from app.alerts.domain import AlertEvent, NotificationPayload, NotificationResult
from app.alerts.manager import AlertManager
from app.alerts.monitoring import observe_connector
from app.alerts.providers.slack import SlackNotificationProvider
from app.alerts.providers.telegram import TelegramNotificationProvider
from app.alerts.providers.webhook import WebhookNotificationProvider
from app.alerts.rules import matches
from app.models.entities import (
    Alert,
    AlertRule,
    AlertRuleChannel,
    ConnectCluster,
    Connector,
    KafkaCluster,
    NotificationChannel,
    NotificationDelivery,
    Pipeline,
    SecretReference,
    Source,
    now,
)
from app.services.secrets import EncryptedDatabaseSecretProvider


def event(status="firing", severity="critical", pipeline_id=None):
    return AlertEvent(
        event_type="CONNECT_TASK_FAILED",
        severity=severity,
        status=status,
        source_type="connector",
        source_id="connector-1",
        source_name="orders-source",
        pipeline_id=pipeline_id,
        pipeline_name="orders-cdc",
        component="kafka-connect",
        message="Task failed",
        details={"connector": "orders-source", "task_id": 0, "error": "Task failed"},
    )


async def test_alert_manager_deduplicates_and_correlates_recovery(db_factory):
    async with db_factory() as db:
        manager = AlertManager(db)
        first = await manager.handle(event())
        duplicate = await manager.handle(event())
        assert first.id == duplicate.id
        assert duplicate.occurrence_count == 2
        assert await db.scalar(select(func.count()).select_from(Alert)) == 1

        resolved = await manager.handle(event(status="resolved"))
        assert resolved.id == first.id
        assert resolved.status == "resolved"
        assert resolved.resolved_at is not None
        assert resolved.resolved_at >= resolved.first_seen_at

        recurrence = await manager.handle(event())
        assert recurrence.id != first.id
        assert await db.scalar(select(func.count()).select_from(Alert)) == 2


def test_rule_engine_matches_severity_event_pipeline_and_disabled_rules():
    rule = AlertRule(
        name="Production critical",
        enabled=True,
        severity="critical",
        event_types=["CONNECT_TASK_FAILED"],
        pipeline_filters=[],
        source_filters=[],
        connector_filters=[],
    )
    assert matches(rule, event())
    rule.severity = "warning"
    assert not matches(rule, event())
    rule.severity = "critical"
    rule.event_types = ["PIPELINE_FAILED"]
    assert not matches(rule, event())
    rule.event_types = []
    rule.pipeline_filters = ["different"]
    assert not matches(rule, event())
    rule.pipeline_filters = []
    rule.enabled = False
    assert not matches(rule, event())


def test_error_classifier_maps_postgres_mysql_and_destination_failures():
    classifier = ErrorClassifier()
    assert (
        classifier.classify('replication slot "orders" does not exist', "CONNECT_TASK_FAILED")
        == "CDC_REPLICATION_SLOT_ERROR"
    )
    assert (
        classifier.classify("binary logging is not enabled", "CONNECT_TASK_FAILED")
        == "CDC_BINLOG_ERROR"
    )
    assert (
        classifier.classify("duplicate key violates constraint", "CONNECT_TASK_FAILED")
        == "DESTINATION_WRITE_FAILED"
    )


async def test_matching_rule_enqueues_each_channel_and_recovery(db_factory):
    async with db_factory() as db:
        secrets = EncryptedDatabaseSecretProvider(db)
        channels = []
        for index, kind in enumerate(("slack", "telegram")):
            reference = await secrets.put_secret({"token": f"secret-{index}"})
            channel = NotificationChannel(
                name=f"channel-{index}", type=kind, config_encrypted=reference
            )
            db.add(channel)
            channels.append(channel)
        rule = AlertRule(
            name="Critical",
            enabled=True,
            severity="critical",
            event_types=["CONNECT_TASK_FAILED"],
            source_filters=[],
            pipeline_filters=[],
            connector_filters=[],
            send_recovery=True,
        )
        db.add(rule)
        await db.flush()
        db.add_all(
            [AlertRuleChannel(alert_rule_id=rule.id, channel_id=channel.id) for channel in channels]
        )
        await AlertManager(db).handle(event())
        assert await db.scalar(select(func.count()).select_from(NotificationDelivery)) == 2
        await AlertManager(db).handle(event(status="resolved"))
        assert await db.scalar(select(func.count()).select_from(NotificationDelivery)) == 4


async def test_cooldown_suppresses_recurrence_until_window_expires(db_factory):
    async with db_factory() as db:
        reference = await EncryptedDatabaseSecretProvider(db).put_secret({"token": "secret"})
        channel = NotificationChannel(name="cooldown", type="slack", config_encrypted=reference)
        rule = AlertRule(
            name="Cooldown",
            enabled=True,
            severity="critical",
            event_types=["CONNECT_TASK_FAILED"],
            source_filters=[],
            pipeline_filters=[],
            connector_filters=[],
            cooldown_seconds=900,
            notification_policy="notify_after_cooldown",
        )
        db.add_all([channel, rule])
        await db.flush()
        db.add(AlertRuleChannel(alert_rule_id=rule.id, channel_id=channel.id))

        manager = AlertManager(db)
        await manager.handle(event())
        first_delivery = await db.scalar(
            select(NotificationDelivery).where(NotificationDelivery.kind == "firing")
        )
        first_delivery.status = "sent"
        first_delivery.sent_at = now()
        await manager.handle(event(status="resolved"))
        await manager.handle(event())
        firing_count = (
            select(func.count())
            .select_from(NotificationDelivery)
            .where(NotificationDelivery.kind == "firing")
        )
        assert await db.scalar(firing_count) == 1

        await manager.handle(event(status="resolved"))
        first_delivery.sent_at = now() - timedelta(minutes=16)
        await manager.handle(event())
        assert await db.scalar(firing_count) == 2


async def test_dispatcher_retries_transient_and_stops_permanent(db_factory, monkeypatch):
    class Provider:
        def __init__(self):
            self.result = NotificationResult(False, False, "Temporary provider failure")

        async def send(self, payload, config):
            return self.result

    provider = Provider()
    monkeypatch.setattr("app.alerts.dispatcher.provider_for", lambda _: provider)
    async with db_factory() as db:
        reference = await EncryptedDatabaseSecretProvider(db).put_secret({"webhook_url": "secret"})
        channel = NotificationChannel(name="retry", type="slack", config_encrypted=reference)
        alert = Alert(
            fingerprint="f" * 64,
            event_type="CONNECT_TASK_FAILED",
            severity="critical",
            source_type="connector",
            source_id="one",
            source_name="one",
            component="kafka-connect",
            title="Task failed",
            message="Task failed",
        )
        db.add_all([channel, alert])
        await db.flush()
        delivery = NotificationDelivery(alert_id=alert.id, channel_id=channel.id)
        db.add(delivery)
        await db.commit()

        assert await dispatch_pending(db) == 1
        assert delivery.status == "pending"
        assert delivery.attempt_count == 1
        delivery.next_attempt_at = now() - timedelta(seconds=1)
        provider.result = NotificationResult(False, True, "Invalid token")
        await db.commit()
        assert await dispatch_pending(db) == 1
        assert delivery.status == "failed"
        assert delivery.attempt_count == 2


async def test_one_provider_failure_does_not_block_other_channels(db_factory, monkeypatch):
    class Provider:
        def __init__(self, success):
            self.success = success

        async def send(self, payload, config):
            return NotificationResult(
                self.success,
                permanent=not self.success,
                error=None if self.success else "Invalid token",
            )

    providers = {"slack": Provider(False), "webhook": Provider(True)}
    monkeypatch.setattr("app.alerts.dispatcher.provider_for", providers.__getitem__)
    async with db_factory() as db:
        secret_provider = EncryptedDatabaseSecretProvider(db)
        failed_channel = NotificationChannel(
            name="failed",
            type="slack",
            config_encrypted=await secret_provider.put_secret({"token": "bad"}),
        )
        good_channel = NotificationChannel(
            name="good",
            type="webhook",
            config_encrypted=await secret_provider.put_secret({"token": "good"}),
        )
        alert = Alert(
            fingerprint="a" * 64,
            event_type="CONNECTOR_FAILED",
            severity="critical",
            source_type="connector",
            source_id="connector",
            source_name="connector",
            component="kafka-connect",
            title="Connector failed",
            message="Connector failed",
        )
        db.add_all([failed_channel, good_channel, alert])
        await db.flush()
        db.add_all(
            [
                NotificationDelivery(alert_id=alert.id, channel_id=failed_channel.id),
                NotificationDelivery(alert_id=alert.id, channel_id=good_channel.id),
            ]
        )
        await db.commit()

        assert await dispatch_pending(db) == 2
        statuses = (
            await db.scalars(
                select(NotificationDelivery.status).order_by(NotificationDelivery.status)
            )
        ).all()
        assert statuses == ["failed", "sent"]


def notification() -> NotificationPayload:
    timestamp = now()
    return NotificationPayload(
        alert_id=uuid4(),
        event_type="CONNECT_TASK_FAILED",
        severity="critical",
        status="firing",
        title="Connect Task Failed",
        message='relation "orders" does not exist',
        source_name="orders-source",
        pipeline_id=None,
        pipeline_name="orders-cdc",
        component="kafka-connect",
        details={"connector": "orders-sink", "task_id": 0},
        started_at=timestamp,
        occurred_at=timestamp,
    )


@respx.mock
async def test_slack_telegram_and_webhook_providers(monkeypatch):
    slack = respx.post("https://hooks.slack.com/services/T/B/X").mock(
        return_value=httpx.Response(200, text="ok")
    )
    telegram = respx.post("https://api.telegram.org/bot123:token/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    webhook = respx.post("https://events.example.com/cluecdc").mock(
        return_value=httpx.Response(202)
    )

    async def allowed(url, resolve_dns=False):
        return None

    monkeypatch.setattr("app.alerts.providers.webhook.validate_outbound_url", allowed)
    assert (
        await SlackNotificationProvider().send(
            notification(), {"webhook_url": "https://hooks.slack.com/services/T/B/X"}
        )
    ).success
    assert (
        await TelegramNotificationProvider().send(
            notification(), {"bot_token": "123:token", "chat_id": "-100"}
        )
    ).success
    assert (
        await WebhookNotificationProvider().send(
            notification(),
            {
                "url": "https://events.example.com/cluecdc",
                "authorization": "Bearer private",
                "headers": {"X-Team": "data"},
            },
        )
    ).success
    assert slack.called and telegram.called and webhook.called
    assert webhook.calls.last.request.headers["authorization"] == "Bearer private"


async def test_channel_api_masks_and_retains_secret(client, db_factory):
    secret = "https://hooks.slack.com/services/T/B/private-value"
    response = await client.post(
        "/api/v1/notification-channels",
        json={
            "name": "Data Engineering",
            "type": "slack",
            "config": {"webhook_url": secret},
        },
    )
    assert response.status_code == 201
    assert secret not in response.text
    assert response.json()["config"] == {
        "configured": True,
        "maskedValue": "https://hooks.slack.com/********",
    }
    identifier = response.json()["id"]
    updated = await client.put(
        f"/api/v1/notification-channels/{identifier}",
        json={"name": "On-call", "config": {"webhook_url": ""}},
    )
    assert updated.status_code == 200
    assert secret not in updated.text
    assert secret not in (await client.get("/api/v1/audit")).text
    async with db_factory() as db:
        channel = await db.get(NotificationChannel, UUID(identifier))
        stored = await EncryptedDatabaseSecretProvider(db).get_secret(channel.config_encrypted)
        assert stored["webhook_url"] == secret
        ciphertext = await db.scalar(select(SecretReference.ciphertext))
        assert secret not in ciphertext


async def test_webhook_ssrf_validation(client):
    for url in (
        "http://example.com/hook",
        "https://localhost/hook",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data",
    ):
        response = await client.post(
            "/api/v1/notification-channels",
            json={"name": url, "type": "webhook", "config": {"url": url}},
        )
        assert response.status_code == 422


async def test_alert_api_paginates_acknowledges_and_silences(client, db_factory):
    async with db_factory() as db:
        alert = Alert(
            fingerprint="b" * 64,
            event_type="PIPELINE_FAILED",
            severity="critical",
            source_type="pipeline",
            source_id="pipeline-one",
            source_name="orders-cdc",
            component="pipeline",
            title="Pipeline Failed",
            message="Pipeline runtime state is FAILED",
        )
        db.add(alert)
        await db.commit()
        identifier = alert.id

    listing = await client.get("/api/v1/alerts?page=1&pageSize=20&status=active")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == str(identifier)

    acknowledged = await client.post(f"/api/v1/alerts/{identifier}/acknowledge")
    assert acknowledged.json()["status"] == "acknowledged"
    silenced = await client.post(
        f"/api/v1/alerts/{identifier}/silence", json={"duration_seconds": 3600}
    )
    assert silenced.json()["status"] == "silenced"
    assert silenced.json()["silenced_until"] is not None
    unsilenced = await client.post(f"/api/v1/alerts/{identifier}/unsilence")
    assert unsilenced.json()["status"] == "firing"
    audit = (await client.get("/api/v1/audit")).text
    assert "alert.acknowledged" in audit
    assert "alert.silenced" in audit


async def test_connector_failed_then_running_creates_and_resolves_alert(db_factory):
    async with db_factory() as db:
        reference = SecretReference(ciphertext="unused")
        kafka = KafkaCluster(name="kafka", bootstrap_servers="kafka:9092")
        db.add_all([reference, kafka])
        await db.flush()
        source = Source(
            name="source",
            type="postgresql",
            environment="test",
            host="source",
            port=5432,
            database_name="commerce",
            username="cdc",
            secret_ref=reference.id,
        )
        connect = ConnectCluster(
            name="connect",
            base_url="http://connect:8083",
            kafka_cluster_id=kafka.id,
        )
        db.add_all([source, connect])
        await db.flush()
        connector = Connector(
            name="orders-source",
            connect_cluster_id=connect.id,
            connector_class="io.debezium.connector.postgresql.PostgresConnector",
            config_json={},
            desired_state="RUNNING",
        )
        db.add(connector)
        await db.flush()
        pipeline = Pipeline(
            name="orders-cdc",
            source_id=source.id,
            kafka_cluster_id=kafka.id,
            connect_cluster_id=connect.id,
            connector_id=connector.id,
            topic_prefix="orders",
            snapshot_mode="initial",
            desired_state="RUNNING",
        )
        db.add(pipeline)
        await db.flush()

        await observe_connector(
            db,
            pipeline=pipeline,
            connector=connector,
            status={
                "connector": {"state": "RUNNING"},
                "tasks": [{"id": 0, "state": "FAILED", "error": "Task failed"}],
            },
            state="DEGRADED",
            desired_state="RUNNING",
        )
        active = await db.scalar(
            select(Alert).where(Alert.event_type == "CONNECT_TASK_FAILED", Alert.status == "firing")
        )
        assert active is not None

        await observe_connector(
            db,
            pipeline=pipeline,
            connector=connector,
            status={
                "connector": {"state": "RUNNING"},
                "tasks": [{"id": 0, "state": "RUNNING"}],
            },
            state="RUNNING",
            desired_state="RUNNING",
        )
        await db.refresh(active)
        assert active.status == "resolved"
        assert active.resolved_at is not None
