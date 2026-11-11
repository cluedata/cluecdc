from app.alerts.domain import AlertEvent
from app.models.entities import AlertRule


def matches(rule: AlertRule, event: AlertEvent) -> bool:
    rule_id = event.details.get("observability_rule_id")
    if rule_id and (str(rule.id) != rule_id or event.event_type not in rule.event_types):
        return False
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
    identities = {connector}
    if rule_id:
        identities.update(
            str(event.details.get(k) or "") for k in ["connector_name", "connector_id"]
        )
    if rule.connector_filters and not identities.intersection(rule.connector_filters):
        return False
    return True
