"""Low-cardinality persisted collector health, visible across API/worker processes."""

from datetime import timedelta

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.entities import ObservabilityCollectionState as State
from app.models.entities import now


async def render(session):
    cutoff = now() - timedelta(seconds=get_settings().observability_stale_seconds)
    row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(State.analysis_json["collections_succeeded"].as_integer()), 0
                ),
                func.coalesce(func.sum(State.analysis_json["collections_failed"].as_integer()), 0),
                func.coalesce(func.max(State.analysis_json["last_duration_seconds"].as_float()), 0),
                func.max(State.last_success_at),
            )
        )
    ).one()
    stale = await session.scalar(
        select(func.count())
        .select_from(State)
        .where((State.last_success_at.is_(None)) | (State.last_success_at < cutoff))
    )

    class Health:
        def collect(self):
            for name, value in [("successes", row[0]), ("failures", row[1])]:
                yield GaugeMetricFamily(
                    "cluecdc_observability_collection_" + name,
                    "Persisted collections for currently tracked pipelines",
                    value=value,
                )
            yield GaugeMetricFamily(
                "cluecdc_observability_collection_duration_seconds",
                "Maximum latest collection duration",
                value=row[2],
            )
            from app.services.observability_analysis import utc

            yield GaugeMetricFamily(
                "cluecdc_observability_last_success_timestamp_seconds",
                "Latest successful lightweight collection; zero means never collected",
                value=utc(row[3]).timestamp() if row[3] else 0,
            )
            yield GaugeMetricFamily(
                "cluecdc_observability_stale_collections",
                "Pipelines without fresh collection",
                value=stale or 0,
            )

    registry = CollectorRegistry()
    registry.register(Health())
    return generate_latest(registry)
