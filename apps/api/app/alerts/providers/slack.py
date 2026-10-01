from typing import Any

import httpx

from app.alerts.domain import NotificationPayload, NotificationResult
from app.alerts.providers.formatting import duration
from app.core.config import get_settings


class SlackNotificationProvider:
    type = "slack"

    async def test(self, config: dict[str, Any]) -> NotificationResult:
        return await self._post(
            config["webhook_url"],
            {"text": "✅ ClueCDC test notification\n\nSlack integration is configured correctly."},
        )

    async def send(
        self, notification: NotificationPayload, config: dict[str, Any]
    ) -> NotificationResult:
        resolved = notification.status == "resolved"
        heading = (
            "🟢 ClueCDC Alert Resolved"
            if resolved
            else (f"🔴 ClueCDC {notification.severity.title()} Alert")
        )
        fields = [
            {"type": "mrkdwn", "text": f"*Pipeline*\n{notification.pipeline_name or 'N/A'}"},
            {"type": "mrkdwn", "text": f"*Event*\n`{notification.event_type}`"},
            {"type": "mrkdwn", "text": f"*Component*\n{notification.component}"},
            {"type": "mrkdwn", "text": f"*Status*\n{'Recovered' if resolved else 'Firing'}"},
        ]
        if resolved:
            fields.append(
                {
                    "type": "mrkdwn",
                    "text": "*Duration*\n"
                    + duration(notification.started_at, notification.resolved_at),
                }
            )
        else:
            connector = notification.details.get("connector")
            task = notification.details.get("task_id")
            if connector:
                fields.append({"type": "mrkdwn", "text": f"*Connector*\n{connector}"})
            if task is not None:
                fields.append({"type": "mrkdwn", "text": f"*Task*\n{task}"})
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": heading}},
            {"type": "section", "fields": fields},
        ]
        if not resolved:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Error*\n```{notification.message[:2500]}```",
                    },
                }
            )
        return await self._post(config["webhook_url"], {"text": heading, "blocks": blocks})

    async def _post(self, url: str, body: dict) -> NotificationResult:
        try:
            async with httpx.AsyncClient(
                timeout=get_settings().integration_timeout_seconds
            ) as client:
                response = await client.post(url, json=body)
            if response.is_success:
                return NotificationResult(True)
            return NotificationResult(
                False,
                response.status_code in {400, 401, 403, 404},
                f"Slack returned HTTP {response.status_code}",
            )
        except httpx.HTTPError:
            return NotificationResult(False, False, "Slack request failed")
