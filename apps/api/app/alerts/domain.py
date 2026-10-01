import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

AlertStatus = Literal["firing", "resolved"]
Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class AlertEvent:
    event_type: str
    severity: Severity
    status: AlertStatus
    source_type: str
    source_id: str | None
    source_name: str | None
    pipeline_id: UUID | None
    pipeline_name: str | None
    component: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None

    @property
    def fingerprint(self) -> str:
        identity = {
            "event_type": self.event_type,
            "source_id": self.source_id,
            "pipeline_id": str(self.pipeline_id) if self.pipeline_id else None,
            "connector": self.details.get("connector"),
            "task_id": self.details.get("task_id"),
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def title(self) -> str:
        return self.event_type.replace("_", " ").title()


@dataclass(frozen=True)
class NotificationPayload:
    alert_id: UUID
    event_type: str
    severity: str
    status: str
    title: str
    message: str
    source_name: str | None
    pipeline_id: UUID | None
    pipeline_name: str | None
    component: str
    details: dict[str, Any]
    started_at: datetime
    occurred_at: datetime
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class NotificationResult:
    success: bool
    permanent: bool = False
    error: str | None = None
