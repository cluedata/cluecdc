from typing import Any, Protocol

from app.alerts.domain import NotificationPayload, NotificationResult


class NotificationProvider(Protocol):
    type: str

    async def test(self, config: dict[str, Any]) -> NotificationResult: ...

    async def send(
        self, notification: NotificationPayload, config: dict[str, Any]
    ) -> NotificationResult: ...
