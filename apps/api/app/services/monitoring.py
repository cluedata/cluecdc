from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass
class PipelineMeasurements:
    throughput: float | None = None
    cdc_lag_ms: int | None = None
    last_event_at: datetime | None = None
    snapshot_state: str | None = None
    snapshot_rows: int | None = None
    error_count: int | None = None
    restart_count: int | None = None


class MetricsProvider(Protocol):
    """Future Prometheus/JMX adapters return measured values with collection timestamps."""

    async def collect(self, pipeline_id: UUID) -> PipelineMeasurements: ...


class UnavailableMetricsProvider:
    async def collect(self, pipeline_id: UUID) -> PipelineMeasurements:
        return PipelineMeasurements()


class SnapshotManager(Protocol):
    """Future incremental/ad-hoc requests use Debezium signaling outside the API stream."""

    async def request_snapshot(self, pipeline_id: UUID, tables: list[str]) -> str: ...
