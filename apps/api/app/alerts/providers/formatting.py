from datetime import datetime

from app.alerts.domain import NotificationPayload


def duration(start: datetime, end: datetime | None) -> str:
    seconds = max(0, int(((end or start) - start).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"


def plain_text(payload: NotificationPayload) -> str:
    if payload.status == "resolved":
        return (
            "🟢 ClueCDC Alert Resolved\n\n"
            f"Pipeline: {payload.pipeline_name or 'N/A'}\n"
            f"Event: {payload.event_type}\nStatus: Recovered\n"
            f"Duration: {duration(payload.started_at, payload.resolved_at)}\n"
            f"Recovered at: {(payload.resolved_at or payload.occurred_at).isoformat()}"
        )
    connector = payload.details.get("connector") or "N/A"
    task = payload.details.get("task_id")
    return (
        f"🔴 ClueCDC {payload.severity.title()} Alert\n\n"
        f"Pipeline: {payload.pipeline_name or 'N/A'}\nConnector: {connector}\n"
        f"Event: {payload.event_type}\nComponent: {payload.component}\n"
        + (f"Task: {task}\n" if task is not None else "")
        + f"\nError:\n{payload.message}\n\nTime: {payload.occurred_at.isoformat()}"
    )
