from app.alerts.domain import AlertEvent
from app.models.entities import AlertRule


def matches(rule: AlertRule, event: AlertEvent) -> bool:
    if not rule.enabled:
        return False
    if rule.severity and rule.severity != event.severity:
        return False
    if rule.event_types and event.event_type not in rule.event_types:
        return False
    if rule.pipeline_filters and str(event.pipeline_id) not in rule.pipeline_filters:
        return False
    if rule.source_filters and str(event.source_id) not in rule.source_filters:
        return False
    connector = str(event.details.get("connector") or "")
    if rule.connector_filters and connector not in rule.connector_filters:
        return False
    return True
