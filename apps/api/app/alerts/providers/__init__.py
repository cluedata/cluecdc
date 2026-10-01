from app.alerts.providers.base import NotificationProvider
from app.alerts.providers.slack import SlackNotificationProvider
from app.alerts.providers.telegram import TelegramNotificationProvider
from app.alerts.providers.webhook import WebhookNotificationProvider


def provider_for(kind: str) -> NotificationProvider:
    providers: dict[str, NotificationProvider] = {
        "slack": SlackNotificationProvider(),
        "telegram": TelegramNotificationProvider(),
        "webhook": WebhookNotificationProvider(),
    }
    return providers[kind]


__all__ = ["NotificationProvider", "provider_for"]
