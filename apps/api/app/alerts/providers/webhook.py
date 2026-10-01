from typing import Any

import httpx

from app.alerts.domain import NotificationPayload, NotificationResult
from app.alerts.security import validate_outbound_url
from app.core.config import get_settings


class WebhookNotificationProvider:
    type = "webhook"

    async def test(self, config: dict[str, Any]) -> NotificationResult:
        return await self._post(
            config,
            {
                "event": "CLUECDC_TEST",
                "status": "test",
                "message": "ClueCDC webhook is configured correctly.",
            },
        )

    async def send(
        self, notification: NotificationPayload, config: dict[str, Any]
    ) -> NotificationResult:
        body = {
            "event": notification.event_type,
            "severity": notification.severity,
            "status": notification.status,
            "timestamp": notification.occurred_at.isoformat(),
            "pipeline": {
                "id": str(notification.pipeline_id) if notification.pipeline_id else None,
                "name": notification.pipeline_name,
            },
            "connector": {
                "name": notification.details.get("connector"),
                "task_id": notification.details.get("task_id"),
            },
            "error": {"message": notification.message, "details": notification.details},
            "alert": {"id": str(notification.alert_id), "title": notification.title},
        }
        return await self._post(config, body)

    async def _post(self, config: dict[str, Any], body: dict) -> NotificationResult:
        try:
            await validate_outbound_url(config["url"], resolve_dns=True)
            headers = dict(config.get("headers") or {})
            if config.get("authorization"):
                headers["Authorization"] = config["authorization"]
            async with httpx.AsyncClient(
                timeout=get_settings().integration_timeout_seconds
            ) as client:
                response = await client.post(config["url"], json=body, headers=headers)
            if response.is_success:
                return NotificationResult(True)
            return NotificationResult(
                False,
                response.status_code in {400, 401, 403, 404, 405, 410, 422},
                f"Webhook returned HTTP {response.status_code}",
            )
        except ValueError:
            return NotificationResult(False, True, "Webhook destination is not allowed")
        except httpx.HTTPError:
            return NotificationResult(False, False, "Webhook request failed")
