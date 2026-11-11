"""Version 1 cached CDC evidence. Unknown measurements are always null."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["AVAILABLE", "UNAVAILABLE", "UNSUPPORTED", "INCONCLUSIVE", "PARTIAL", "STALE"]
Confidence = Literal["DIRECT", "INDIRECT", "UNKNOWN"]


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    value: float | int | None = None
    unit: str
    status: Status = "UNAVAILABLE"
    confidence: Confidence = "UNKNOWN"
    observed_at: datetime | None = None
    collected_at: datetime
    source: str
    reason_code: str

    @model_validator(mode="after")
    def evidence(self):
        if self.status != "AVAILABLE" and self.value is not None:
            raise ValueError("Unavailable evidence cannot contain a numeric value")
        if self.status == "AVAILABLE" and (self.value is None or self.observed_at is None):
            raise ValueError("Available evidence requires a value and timestamp")
        return self


class PartitionObservation(BaseModel):
    topic: str
    partition: int
    beginning_offset: int | None = None
    end_offset: int | None = None
    committed_offset: int | None = None
    backlog: Metric


class DeliveryObservation(BaseModel):
    delivery_id: UUID
    name: str
    expected_running: bool
    connector_id: UUID | None = None
    connector_name: str | None = None
    connector_state: str = "UNKNOWN"
    operational_state: str = "UNKNOWN"
    task_states: dict[str, str] = Field(default_factory=dict)
    group_id: str | None = None
    group_state: str | None = None
    group_provenance: str | None = None
    group_generation: str | None = None
    task_ids: list[int] = Field(default_factory=list)
    required_tasks: int = 0
    verified_task_workers: int = 0
    required_partitions: int = 0
    valid_partitions: int = 0
    partitions: list[PartitionObservation] = Field(default_factory=list)
    backlog: Metric
    sink_metrics: dict[str, Metric] = Field(default_factory=dict)
    destination_progress: Metric


class SnapshotObservation(BaseModel):
    table_id: UUID
    table: str
    kind: str
    state: str
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    completed_at: datetime | None = None
    processed_rows: Metric
    estimated_rows: int | None = None
    current_chunk: str | None = None
    percentage: float | None = None
    provenance: str
    stale: bool
    running: Metric | None = None
    paused: Metric | None = None
    progress_counter: Metric | None = None


class Finding(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    pipeline_id: UUID
    delivery_id: UUID | None = None
    observations: list[str]
    observed_at: datetime
    confidence: Confidence
    explanation: str
    suggested_action: str


class Observation(BaseModel):
    schema_version: Literal[1] = 1
    pipeline_id: UUID
    name: str
    source_provider: str
    source_id: UUID | None = None
    operational_state: str
    connector_state: str
    capture_expected_running: bool = False
    connector_id: UUID | None = None
    connector_name: str | None = None
    task_ids: list[int] = Field(default_factory=list)
    task_states: dict[str, str] = Field(default_factory=dict)
    context: str = "streaming"
    collected_at: datetime | None
    last_success_at: datetime | None = None
    status: Status = "UNAVAILABLE"
    capture_lag: Metric
    capture_events: Metric
    source_connected: Metric | None = None
    capture_event_age: Metric | None = None
    deliveries: list[DeliveryObservation] = Field(default_factory=list)
    snapshots: list[SnapshotObservation] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


class HistoryGap(BaseModel):
    start: datetime
    end: datetime
    reason_code: str


class History(BaseModel):
    schema_version: Literal[1] = 1
    pipeline_id: UUID
    start: datetime
    end: datetime
    points: list[Observation]
    gaps: list[HistoryGap]
    truncated: bool


class Capabilities(BaseModel):
    pipeline_id: UUID
    source_provider: str
    capture: str
    backlog: str
    sink_processing: str
    destination_visibility: str
    snapshot: str
