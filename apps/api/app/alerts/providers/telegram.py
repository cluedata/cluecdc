from typing import Any

import httpx

from app.alerts.domain import NotificationPayload, NotificationResult
from app.alerts.providers.formatting import plain_text
from app.core.config import get_settings


class TelegramNotificationProvider:
    type = "telegram"

    async def test(self, config: dict[str, Any]) -> NotificationResult:
        return await self._post(
            config, "✅ ClueCDC test notification\n\nTelegram integration is configured correctly."
        )

    async def send(
        self, notification: NotificationPayload, config: dict[str, Any]
    ) -> NotificationResult:
        return await self._post(config, plain_text(notification))

    async def _post(self, config: dict[str, Any], text: str) -> NotificationResult:
        # Token is deliberately never included in logs or returned errors.
        url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
        try:
            async with httpx.AsyncClient(
                timeout=get_settings().integration_timeout_seconds
            ) as client:
                response = await client.post(url, json={"chat_id": config["chat_id"], "text": text})
            if response.is_success:
                return NotificationResult(True)
            return NotificationResult(
                False,
                response.status_code in {400, 401, 403, 404},
                f"Telegram returned HTTP {response.status_code}",
            )
        except httpx.HTTPError:
            return NotificationResult(False, False, "Telegram request failed")
