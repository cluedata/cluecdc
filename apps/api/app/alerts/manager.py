from datetime import UTC, timedelta

import structlog
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.domain import AlertEvent
from app.alerts.metrics import ALERTS_ACTIVE, ALERTS_TOTAL
from app.alerts.rules import matches
from app.models.entities import (
    Alert,
    AlertRule,
    AlertRuleChannel,
    NotificationChannel,
    NotificationDelivery,
    now,
)

log = structlog.get_logger(module="alerting")
ACTIVE_STATUSES = ("firing", "acknowledged", "silenced")


class AlertManager:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def handle(self, event: AlertEvent) -> Alert | None:
        """Persist an observation. Provider failures can never propagate through this path."""
        try:
            async with self.session.begin_nested():
                return await self._handle(event)
        except Exception as exc:
            # Alerting is best-effort observability. Do not fail CDC control-plane work.
            log.error(
                "alert_event_failed",
                event_type=event.event_type,
                error_type=type(exc).__name__,
            )
            return None

    async def _handle(self, event: AlertEvent) -> Alert | None:
        active = await self.session.scalar(
            select(Alert)
            .where(Alert.fingerprint == event.fingerprint, Alert.status.in_(ACTIVE_STATUSES))
            .order_by(desc(Alert.created_at))
            .with_for_update()
            .limit(1)
        )
        observed_at = event.occurred_at or now()
        if event.status == "resolved":
            if not active:
                await self._refresh_active_metric(event.event_type, event.severity)
                return None
            active.status = "resolved"
            active.last_seen_at = observed_at
            active.resolved_at = observed_at
            await self._enqueue(active, event, recovery=True)
            await self._refresh_active_metric(active.event_type, active.severity)
            log.info("alert_resolved", alert_id=str(active.id), event_type=active.event_type)
            return active

        if active:
            active.last_seen_at = observed_at
            active.occurrence_count += 1
            active.message = event.message
            active.details = event.details
            if (
                active.status == "silenced"
                and active.silenced_until
                and active.silenced_until <= now()
            ):
                active.status = "firing"
                active.silenced_until = None
            await self._refresh_active_metric(active.event_type, active.severity)
            return active

        alert = Alert(
            fingerprint=event.fingerprint,
            event_type=event.event_type,
            severity=event.severity,
            status="firing",
            source_type=event.source_type,
            source_id=event.source_id,
            source_name=event.source_name,
            pipeline_id=event.pipeline_id,
            pipeline_name=event.pipeline_name,
            component=event.component,
            title=event.title,
            message=event.message,
            details=event.details,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )
        self.session.add(alert)
        await self.session.flush()
        ALERTS_TOTAL.labels(alert.severity, alert.event_type).inc()
        await self._enqueue(alert, event, recovery=False)
        await self._refresh_active_metric(alert.event_type, alert.severity)
        log.info("alert_created", alert_id=str(alert.id), event_type=alert.event_type)
        return alert

    async def _enqueue(self, alert: Alert, event: AlertEvent, recovery: bool) -> None:
        rules = (await self.session.scalars(select(AlertRule))).all()
        for rule in rules:
            if not matches(rule, event) or (recovery and not rule.send_recovery):
                continue
            channels = (
                await self.session.scalars(
                    select(AlertRuleChannel.channel_id)
                    .join(
                        NotificationChannel,
                        NotificationChannel.id == AlertRuleChannel.channel_id,
                    )
                    .where(
                        AlertRuleChannel.alert_rule_id == rule.id,
                        NotificationChannel.enabled.is_(True),
                    )
                )
            ).all()
            for channel_id in channels:
                if not recovery and not await self._cooldown_allows(
                    alert, channel_id, rule.notification_policy, rule.cooldown_seconds
                ):
                    continue
                exists = await self.session.scalar(
                    select(NotificationDelivery.id).where(
                        NotificationDelivery.alert_id == alert.id,
                        NotificationDelivery.channel_id == channel_id,
                        NotificationDelivery.kind == ("resolved" if recovery else "firing"),
                    )
                )
                if not exists:
                    self.session.add(
                        NotificationDelivery(
                            alert_id=alert.id,
                            channel_id=channel_id,
                            kind="resolved" if recovery else "firing",
                        )
                    )

    async def _cooldown_allows(
        self, alert: Alert, channel_id, policy: str, cooldown_seconds: int
    ) -> bool:
        if policy == "notify_every_occurrence":
            return True
        previous = await self.session.scalar(
            select(NotificationDelivery.sent_at)
            .join(Alert, Alert.id == NotificationDelivery.alert_id)
            .where(
                Alert.fingerprint == alert.fingerprint,
                Alert.id != alert.id,
                NotificationDelivery.channel_id == channel_id,
                NotificationDelivery.kind == "firing",
                NotificationDelivery.status == "sent",
            )
            .order_by(NotificationDelivery.sent_at.desc())
            .limit(1)
        )
        if not previous:
            return True
        if policy == "notify_first_occurrence_only":
            return False
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        return previous <= now() - timedelta(seconds=cooldown_seconds)

    async def _refresh_active_metric(self, event_type: str, severity: str) -> None:
        count = await self.session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.event_type == event_type,
                Alert.severity == severity,
                Alert.status.in_(ACTIVE_STATUSES),
            )
        )
        ALERTS_ACTIVE.labels(severity, event_type).set(count or 0)
