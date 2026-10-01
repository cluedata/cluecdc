import time
from datetime import timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.domain import NotificationPayload
from app.alerts.metrics import DELIVERY_DURATION, NOTIFICATIONS_FAILED, NOTIFICATIONS_SENT
from app.alerts.providers import provider_for
from app.models.entities import Alert, NotificationChannel, NotificationDelivery, now
from app.services.secrets import secret_provider

log = structlog.get_logger(module="alerting")
RETRY_DELAYS = (5, 30, 120)
# One initial delivery plus three bounded retries.
MAX_ATTEMPTS = 4


def payload_for(alert: Alert, kind: str) -> NotificationPayload:
    occurred = alert.resolved_at if kind == "resolved" else alert.first_seen_at
    return NotificationPayload(
        alert_id=alert.id,
        event_type=alert.event_type,
        severity=alert.severity,
        status=kind,
        title=alert.title,
        message=alert.message,
        source_name=alert.source_name,
        pipeline_id=alert.pipeline_id,
        pipeline_name=alert.pipeline_name,
        component=alert.component,
        details=alert.details,
        started_at=alert.first_seen_at,
        occurred_at=occurred or now(),
        resolved_at=alert.resolved_at,
    )


async def dispatch_pending(session: AsyncSession, limit: int = 20) -> int:
    deliveries = (
        await session.scalars(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.status == "pending",
                NotificationDelivery.next_attempt_at <= now(),
            )
            .order_by(NotificationDelivery.next_attempt_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    ).all()
    processed = 0
    for delivery in deliveries:
        alert = await session.get(Alert, delivery.alert_id)
        channel = await session.get(NotificationChannel, delivery.channel_id)
        if not alert or not channel or not channel.enabled:
            delivery.status = "failed"
            delivery.last_error = "Notification channel is unavailable or disabled"
            processed += 1
            continue
        if alert.status == "silenced" and delivery.kind == "firing":
            delivery.status = "failed"
            delivery.last_error = "Suppressed by alert silence"
            processed += 1
            continue
        delivery.status = "sending"
        delivery.attempt_count += 1
        await session.flush()
        started = time.monotonic()
        try:
            config = await secret_provider(session).get_secret(channel.config_encrypted)
            result = await provider_for(channel.type).send(
                payload_for(alert, delivery.kind), config
            )
        except Exception as exc:
            # Never persist exception text: request URLs may contain credentials.
            log.warning(
                "notification_provider_error",
                alert_id=str(alert.id),
                channel_id=str(channel.id),
                provider=channel.type,
                error_type=type(exc).__name__,
            )
            result = None
        elapsed = time.monotonic() - started
        if result and result.success:
            delivery.status = "sent"
            delivery.sent_at = now()
            delivery.last_error = None
            NOTIFICATIONS_SENT.labels(channel.type).inc()
            DELIVERY_DURATION.labels(channel.type, "sent").observe(elapsed)
            log.info(
                "notification_sent",
                alert_id=str(alert.id),
                channel_id=str(channel.id),
                provider=channel.type,
                duration_ms=round(elapsed * 1000),
            )
        else:
            permanent = bool(result and result.permanent)
            delivery.last_error = (
                result.error if result and result.error else "Provider request failed"
            )
            NOTIFICATIONS_FAILED.labels(channel.type).inc()
            DELIVERY_DURATION.labels(channel.type, "failed").observe(elapsed)
            if permanent or delivery.attempt_count >= MAX_ATTEMPTS:
                delivery.status = "failed"
            else:
                delivery.status = "pending"
                delivery.next_attempt_at = now() + timedelta(
                    seconds=RETRY_DELAYS[delivery.attempt_count - 1]
                )
            log.warning(
                "notification_failed",
                alert_id=str(alert.id),
                channel_id=str(channel.id),
                provider=channel.type,
                attempt=delivery.attempt_count,
                permanent=permanent,
            )
        processed += 1
    await session.commit()
    return processed
