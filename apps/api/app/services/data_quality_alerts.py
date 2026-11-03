"""Rule-level incident routing through the existing alert manager and outbox."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select

from app.alerts.domain import AlertEvent
from app.alerts.manager import AlertManager
from app.core.errors import DomainError
from app.models.entities import Alert, NotificationChannel, NotificationDelivery, now
from app.services.data_quality_engine import aware


class QualityAlertManager(AlertManager):
    def __init__(self, session, options):
        super().__init__(session)
        self.options = options

    async def _enqueue(self, alert, event, recovery):
        options = self.options
        if recovery and not options.send_recovery:
            return
        channel = await self.session.get(NotificationChannel, options.channel_id)
        if not channel or not channel.enabled:
            return
        if not recovery:
            previous = await self.session.scalar(
                select(NotificationDelivery.created_at)
                .join(Alert)
                .where(
                    Alert.fingerprint == alert.fingerprint,
                    Alert.id != alert.id,
                    NotificationDelivery.channel_id == channel.id,
                    NotificationDelivery.kind == "firing",
                    NotificationDelivery.status.in_(["pending", "sending", "sent"]),
                )
                .order_by(NotificationDelivery.created_at.desc())
                .limit(1)
            )
            if previous and aware(previous) > now() - timedelta(seconds=options.cooldown_seconds):
                return
        if not recovery and not await self._cooldown_allows(
            alert, channel.id, "notify_after_cooldown", options.cooldown_seconds
        ):
            return
        kind = "resolved" if recovery else "firing"
        exists = await self.session.scalar(
            select(NotificationDelivery.id).where(
                NotificationDelivery.alert_id == alert.id,
                NotificationDelivery.channel_id == channel.id,
                NotificationDelivery.kind == kind,
            )
        )
        if not exists:
            self.session.add(
                NotificationDelivery(alert_id=alert.id, channel_id=channel.id, kind=kind)
            )


async def observe(session, check, run, results, config):
    options = config.alerting
    if not options.enabled or not options.channel_id or check.version != run.version:
        return
    state = dict(check.alert_state_json or {})
    manager = QualityAlertManager(session, options)
    for result in results:
        key = result["rule_id"]
        previous = dict(state.get(key, {}))
        if previous.get("last_run") == str(run.id):
            continue
        quality = result["quality"]
        failures = previous.get("failures", 0)
        if quality == "FAIL":
            failures += 1
        elif quality == "PASS":
            failures = 0
        previous.update(failures=failures, last_run=str(run.id))
        for path, enabled, firing in (
            ("FAIL", options.on_fail, quality == "FAIL" and failures >= options.failure_threshold),
            ("ERROR", options.on_error, quality == "ERROR"),
            ("INCONCLUSIVE", options.on_inconclusive, quality == "INCONCLUSIVE"),
        ):
            if not enabled or (not firing and quality != "PASS"):
                continue
            event = AlertEvent(
                event_type=f"DATA_QUALITY_{path}",
                severity=options.severity,
                status="firing" if firing else "resolved",
                source_type="data_quality",
                source_id=f"{check.id}:{key}",
                source_name=check.name,
                pipeline_id=config.pipeline_id,
                pipeline_name=None,
                component="data_quality",
                message=f"{result['rule_type']}: {quality} (BEST_EFFORT)",
                details={
                    "check_id": str(check.id),
                    "rule_id": key,
                    "run_id": str(run.id),
                    "quality": quality,
                    "consistency": "BEST_EFFORT",
                },
            )
            alert = await manager.handle(event)
            if firing and alert is None:
                raise DomainError(
                    "DQ_ALERT_PERSISTENCE", "Data Quality alert persistence failed", 503
                )
        state[key] = previous
    check.alert_state_json = state


async def channel_status(session, config: dict) -> dict:
    options = config.get("alerting", {})
    identifier = options.get("channel_id")
    channel = await session.get(NotificationChannel, UUID(identifier)) if identifier else None
    return {
        "configured": bool(options.get("enabled")),
        "available": bool(channel and channel.enabled),
        "channel_name": channel.name if channel else None,
        "reason": None if channel and channel.enabled else "No enabled notification channel",
    }
