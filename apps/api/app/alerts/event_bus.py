from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.domain import AlertEvent


class AlertEventBus(Protocol):
    async def emit(self, session: AsyncSession, event: AlertEvent) -> None: ...


class InternalAlertEventBus:
    """In-process implementation; the protocol permits a Kafka/Redis/NATS replacement."""

    async def emit(self, session: AsyncSession, event: AlertEvent) -> None:
        # Imported lazily so domain event producers do not depend on manager internals.
        from app.alerts.manager import AlertManager

        await AlertManager(session).handle(event)


alert_event_bus: AlertEventBus = InternalAlertEventBus()
