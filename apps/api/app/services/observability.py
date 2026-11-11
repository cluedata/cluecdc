"""Persisted observability, bounded history, and read-only collection."""

import asyncio
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.connect import KafkaConnectClient
from app.core.config import get_settings
from app.core.errors import DomainError
from app.models.entities import (
    ConnectCluster,
    Connection,
    Connector,
    KafkaCluster,
    ObservabilityCollectionState,
    Pipeline,
    PipelineDestination,
    PipelineMetric,
    PipelineOperation,
    PipelineTable,
    WarehouseBatchLedger,
    now,
)
from app.schemas.observability import (
    Capabilities,
    DeliveryObservation,
    History,
    HistoryGap,
    Observation,
    SnapshotObservation,
)
from app.services.observability_analysis import analyse, continuity_seconds, utc
from app.services.observability_providers import (
    Exporters,
    measured,
    metric,
    provider_ready,
    provider_result,
    sample_backlog,
)
from app.services.secrets import kafka_credentials


async def empty(session: AsyncSession, pipeline: Pipeline) -> Observation:
    source = await session.get(Connection, pipeline.source_id)
    connector = (
        await session.get(Connector, pipeline.connector_id) if pipeline.connector_id else None
    )
    return Observation(
        pipeline_id=pipeline.id,
        name=pipeline.name,
        source_provider=source.type if source else "unknown",
        source_id=pipeline.source_id,
        connector_name=connector.name if connector else None,
        operational_state=pipeline.actual_state,
        connector_state=connector.actual_state if connector else "UNKNOWN",
        capture_expected_running=bool(
            connector and pipeline.desired_state == "RUNNING" and connector.operation_token is None
        ),
        connector_id=pipeline.connector_id,
        collected_at=None,
        capture_lag=metric("milliseconds", "debezium_jmx", "NOT_COLLECTED"),
        capture_events=metric("events", "debezium_jmx", "NOT_COLLECTED"),
    )


def fresh_view(sample: Observation, timestamp=None) -> Observation:
    sample = sample.model_copy(deep=True)
    timestamp = timestamp or now()
    stale = sample.collected_at is not None and (
        utc(timestamp) - utc(sample.collected_at)
    ).total_seconds() > (get_settings().observability_stale_seconds)
    if stale:
        sample.status = "STALE"
        sample.findings = []  # Time-sensitive diagnoses must not remain current indefinitely.
    metrics = [sample.capture_lag, sample.capture_events]
    metrics.extend(m for m in [sample.source_connected, sample.capture_event_age] if m is not None)
    for d in sample.deliveries:
        metrics.extend([d.backlog, d.destination_progress, *d.sink_metrics.values()])
        metrics.extend(p.backlog for p in d.partitions)
    for s in sample.snapshots:
        if s.state != "COMPLETED":
            metrics.append(s.processed_rows)
        metrics.extend(m for m in [s.running, s.paused, s.progress_counter] if m is not None)
        s.stale = s.stale or stale
    for m in metrics:
        if m.status == "AVAILABLE" and (
            stale
            or (
                m.observed_at
                and (utc(timestamp) - utc(m.observed_at)).total_seconds()
                > get_settings().observability_stale_seconds
            )
        ):
            m.status, m.value, m.reason_code = "STALE", None, "OBSERVATION_EXPIRED"
    return sample


async def latest(session, pipeline):
    state = await session.get(ObservabilityCollectionState, pipeline.id)
    if state and state.latest_json:
        result = Observation.model_validate(state.latest_json)
        result.last_success_at = state.last_success_at
        result.operational_state = pipeline.actual_state
        return fresh_view(result)
    return await empty(session, pipeline)


async def history(session, pipeline_id, start, end, limit=500):
    timestamp = now()
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start >= end
        or utc(start) < timestamp - timedelta(days=7)
        or utc(end) > timestamp + timedelta(seconds=1)
    ):
        raise DomainError(
            "INVALID_OBSERVABILITY_RANGE", "Use an ordered UTC range within seven days", 422
        )
    start, end = utc(start), utc(end)
    rows = list(
        (
            await session.scalars(
                select(PipelineMetric)
                .where(
                    PipelineMetric.pipeline_id == pipeline_id,
                    PipelineMetric.sample_bucket.is_not(None),
                    PipelineMetric.created_at >= start,
                    PipelineMetric.created_at <= end,
                )
                .order_by(PipelineMetric.created_at)
                .limit(limit + 1)
            )
        ).all()
    )
    points = [Observation.model_validate(row.values_json) for row in rows[:limit]]
    gaps = []
    cursor = start
    for point in points:
        assert point.collected_at is not None
        if (utc(point.collected_at) - cursor).total_seconds() > continuity_seconds():
            gaps.append(HistoryGap(start=cursor, end=point.collected_at, reason_code="SAMPLE_GAP"))
        cursor = utc(point.collected_at)
    if (end - cursor).total_seconds() > continuity_seconds():
        gaps.append(
            HistoryGap(
                start=cursor,
                end=end,
                reason_code="POINT_LIMIT" if len(rows) > limit else "SAMPLE_GAP",
            )
        )
    return History(
        pipeline_id=pipeline_id,
        start=start,
        end=end,
        points=points,
        gaps=gaps,
        truncated=len(rows) > limit,
    )


async def capabilities(session, pipeline):
    source = await session.get(Connection, pipeline.source_id)
    provider = source.type if source else "unknown"
    configured = str(pipeline.connect_cluster_id) in get_settings().observability_exporters
    return Capabilities(
        pipeline_id=pipeline.id,
        source_provider=provider,
        capture=(
            "CONFIGURED_REQUIRES_VERIFIED_COVERAGE" if configured else "EXPORTER_NOT_CONFIGURED"
        )
        if provider in {"postgresql", "mysql"}
        else "UNSUPPORTED",
        backlog="TARGETED_VERIFIED_GROUPS",
        sink_processing="TRUSTED_EXPORTER_REQUIRED",
        destination_visibility="SCOPED_WAREHOUSE_LEDGER_ONLY; JDBC_UNSUPPORTED",
        snapshot="PERSISTED_METADATA; NOTIFICATION_PROVENANCE_WHEN_PRESENT",
    )


async def collect(session: AsyncSession, pipeline: Pipeline, *, result=None) -> Observation:
    result = result or await empty(session, pipeline)
    result.collected_at = now()
    deliveries = (
        await session.scalars(
            select(PipelineDestination).where(PipelineDestination.pipeline_id == pipeline.id)
        )
    ).all()
    result.deliveries = []
    for delivery in deliveries:
        d = DeliveryObservation(
            delivery_id=delivery.id,
            name=delivery.name,
            expected_running=delivery.desired_state == "RUNNING"
            and pipeline.desired_state == "RUNNING",
            connector_id=delivery.connector_id,
            connector_state=delivery.actual_state,
            operational_state=delivery.actual_state,
            backlog=metric("records", "kafka_admin", "NOT_COLLECTED"),
            destination_progress=metric(
                "batches", "destination", "NO_DURABLE_MARKER", status="UNSUPPORTED"
            ),
        )
        result.deliveries.append(d)
    await session.commit()  # Release the metadata connection before provider I/O.
    settings = get_settings()
    exporter = Exporters()
    workers = settings.observability_exporters.get(str(pipeline.connect_cluster_id), {})
    try:
        await exporter.scrape(workers)
    except TimeoutError:
        pass
    source_connector = (
        await session.get(Connector, pipeline.connector_id) if pipeline.connector_id else None
    )
    tasks = []
    if source_connector:
        cluster = await session.get(ConnectCluster, source_connector.connect_cluster_id)
        assert cluster is not None
        await session.commit()
        try:
            if not provider_ready("connect", str(source_connector.id)):
                raise DomainError(
                    "OBS_PROVIDER_BACKOFF", "Read-only runtime collection is backing off", 503
                )
            async with asyncio.timeout(settings.observability_provider_timeout_seconds):
                runtime = await KafkaConnectClient(cluster.base_url).status(source_connector.name)
            provider_result("connect", str(source_connector.id), success=True)
            tasks = runtime.get("tasks", [])
            result.connector_state = runtime.get("connector", {}).get("state", "UNKNOWN")
        except (DomainError, TimeoutError) as exc:
            if not isinstance(exc, DomainError) or exc.code != "OBS_PROVIDER_BACKOFF":
                provider_result("connect", str(source_connector.id), success=False)
            result.connector_state = "UNKNOWN"
    result.task_ids = [t["id"] for t in tasks if isinstance(t.get("id"), int)]
    result.task_states = {
        str(t["id"]): str(t.get("state", "UNKNOWN")) for t in tasks if isinstance(t.get("id"), int)
    }
    # Debezium's server label is topic.prefix. Ambiguous duplicate server names are rejected.
    peers = (
        await session.scalars(
            select(Connector).where(
                Connector.connect_cluster_id == pipeline.connect_cluster_id,
                Connector.connector_type == "source",
            )
        )
    ).all()
    server = source_connector.config_json.get("topic.prefix") if source_connector else None
    attributable = server and sum(c.config_json.get("topic.prefix") == server for c in peers) == 1
    provider = "postgres" if result.source_provider == "postgresql" else result.source_provider
    attributable = attributable and result.source_provider in {"postgresql", "mysql"}
    if result.source_provider not in {"postgresql", "mysql"}:
        result.capture_lag = metric(
            "milliseconds", "debezium_jmx", "PROVIDER_UNSUPPORTED", status="UNSUPPORTED"
        )
    elif attributable:
        provider = "postgres" if result.source_provider == "postgresql" else "mysql"
        result.capture_lag = exporter.capture(
            tasks, server, provider, "cluecdc_capture_lag_ms", "milliseconds"
        )
        result.capture_events = exporter.capture(
            tasks, server, provider, "cluecdc_capture_events_total", "events"
        )
        result.source_connected = exporter.capture(
            tasks, server, provider, "cluecdc_source_connected", "boolean"
        )
        result.capture_event_age = exporter.capture(
            tasks, server, provider, "cluecdc_capture_event_age_ms", "milliseconds"
        )
        if result.source_connected.status == "AVAILABLE" and result.source_connected.value == 0:
            result.capture_lag = metric("milliseconds", "debezium_jmx", "SOURCE_DISCONNECTED")
    tables = (
        await session.scalars(
            select(PipelineTable).where(
                PipelineTable.pipeline_id == pipeline.id, PipelineTable.removed_at.is_(None)
            )
        )
    ).all()
    operations = (
        await session.scalars(
            select(PipelineOperation)
            .where(PipelineOperation.pipeline_id == pipeline.id)
            .order_by(PipelineOperation.created_at.desc())
            .limit(1000)
        )
    ).all()
    for table in tables:
        operation = next((o for o in operations if o.table_id == table.id), None)
        state = {"CANCELLED": "UNKNOWN", "NOT_REQUIRED": "UNKNOWN"}.get(
            table.snapshot_status, table.snapshot_status
        )
        if (
            table.snapshot_status == "CANCELLED"
            and (table.snapshot_observation_json or {}).get("event") == "ABORTED"
        ):
            state = "ABORTED"
        progress = table.snapshot_last_activity_at
        row_metric = (
            measured(table.snapshot_rows_processed, "rows", "pipeline_table", progress)
            if table.snapshot_rows_processed is not None and progress
            else metric("rows", "pipeline_table", "PROGRESS_NOT_OBSERVED")
        )
        # Estimated cardinality is not a trustworthy denominator for a percent-complete claim.
        result.snapshots.append(
            SnapshotObservation(
                table_id=table.id,
                table=f"{table.schema_name}.{table.table_name}",
                kind="RESNAPSHOT"
                if operation and operation.type == "RESYNC_TABLE"
                else "INCREMENTAL"
                if operation
                else "INITIAL",
                state=state
                if state in {"PENDING", "RUNNING", "COMPLETED", "FAILED", "STOPPED", "ABORTED"}
                else "UNKNOWN",
                started_at=table.snapshot_started_at,
                last_progress_at=progress,
                completed_at=table.snapshot_finished_at,
                processed_rows=row_metric,
                estimated_rows=table.snapshot_estimated_rows
                if table.snapshot_estimated_rows is not None and table.snapshot_estimated_rows >= 0
                else None,
                current_chunk=table.snapshot_current_chunk,
                provenance="debezium_notification"
                if table.snapshot_observation_json
                else "pipeline_table_metadata",
                stale=bool(
                    progress
                    and state == "RUNNING"
                    and (now() - utc(progress)).total_seconds()
                    > settings.observability_stale_seconds
                ),
                running=exporter.capture(
                    tasks, server, provider, "cluecdc_snapshot_running", "boolean", "snapshot"
                )
                if attributable
                else None,
                paused=exporter.capture(
                    tasks, server, provider, "cluecdc_snapshot_paused", "boolean", "snapshot"
                )
                if attributable
                else None,
                progress_counter=exporter.capture(
                    tasks, server, provider, "cluecdc_capture_events_total", "events", "snapshot"
                )
                if attributable
                else None,
            )
        )
    kafka = await session.get(KafkaCluster, pipeline.kafka_cluster_id)
    assert kafka is not None
    credentials = await kafka_credentials(session, kafka)
    for index, delivery in enumerate(deliveries):
        d = result.deliveries[index]
        connector = (
            await session.get(Connector, delivery.connector_id) if delivery.connector_id else None
        )
        if connector:
            d.connector_name = connector.name
            d.expected_running = d.expected_running and connector.operation_token is None
            cluster = await session.get(ConnectCluster, connector.connect_cluster_id)
            assert cluster is not None
            await session.commit()
            phase = "connect"
            try:
                if not provider_ready("connect", str(connector.id)):
                    raise DomainError(
                        "OBS_PROVIDER_BACKOFF", "Read-only runtime collection is backing off", 503
                    )
                async with asyncio.timeout(settings.observability_provider_timeout_seconds):
                    client = KafkaConnectClient(cluster.base_url)
                    runtime, config = await asyncio.gather(
                        client.status(connector.name), client.raw_config(connector.name)
                    )
                provider_result("connect", str(connector.id), success=True)
                d.connector_state = runtime.get("connector", {}).get("state", "UNKNOWN")
                # Delivery may use a different Connect cluster. Never mix workers across clusters.
                sink_exporter = exporter
                if connector.connect_cluster_id != pipeline.connect_cluster_id:
                    sink_exporter = Exporters()
                    try:
                        await sink_exporter.scrape(
                            settings.observability_exporters.get(
                                str(connector.connect_cluster_id), {}
                            )
                        )
                    except TimeoutError:
                        # Keep Kafka progress when the exporter times out.
                        pass
                d.sink_metrics = sink_exporter.sink(runtime.get("tasks", []), connector.name)
                task_status = runtime.get("tasks", [])
                d.task_states = {
                    str(t["id"]): str(t.get("state", "UNKNOWN"))
                    for t in task_status
                    if isinstance(t.get("id"), int)
                }
                d.task_ids = [t["id"] for t in task_status if isinstance(t.get("id"), int)]
                d.required_tasks = len(task_status)
                d.verified_task_workers = sum(
                    t.get("worker_id") in sink_exporter.samples for t in task_status
                )
                working = d.model_copy(deep=True)
                phase = "kafka"
                if not provider_ready("kafka", str(delivery.id)):
                    raise DomainError(
                        "OBS_PROVIDER_BACKOFF", "Read-only offset collection is backing off", 503
                    )
                async with asyncio.timeout(settings.observability_provider_timeout_seconds):
                    await asyncio.to_thread(
                        sample_backlog,
                        kafka,
                        credentials,
                        config,
                        connector.name,
                        working,
                        runtime.get("tasks", []),
                    )
                d = working
                provider_result("kafka", str(delivery.id), success=True)
            except Exception as exc:
                backing_off = isinstance(exc, DomainError) and exc.code == "OBS_PROVIDER_BACKOFF"
                if not backing_off:
                    provider_result(
                        phase,
                        str(connector.id if phase == "connect" else delivery.id),
                        success=False,
                    )
                d.backlog = metric(
                    "records",
                    "kafka_admin",
                    "PROVIDER_BACKOFF" if backing_off else "PROVIDER_UNAVAILABLE",
                )
        else:
            ledger = await session.scalar(
                select(WarehouseBatchLedger)
                .where(
                    WarehouseBatchLedger.delivery_id == delivery.id,
                    WarehouseBatchLedger.status.in_(["APPLIED", "CHECKPOINTED"]),
                    WarehouseBatchLedger.applied_at.is_not(None),
                )
                .order_by(WarehouseBatchLedger.applied_at.desc())
                .limit(1)
            )
            if ledger:
                # A persisted apply timestamp proves only this delivery's staged batch apply.
                d.destination_progress = measured(
                    1, "last_applied_batch", "warehouse_ledger", ledger.applied_at
                )
        result.deliveries[index] = d
    result.collected_at = now()
    result.last_success_at = result.collected_at
    metrics = [
        result.capture_lag,
        *(
            m
            for d in result.deliveries
            for m in [d.backlog, d.destination_progress, *d.sink_metrics.values()]
        ),
    ]
    result.status = "AVAILABLE" if all(m.status == "AVAILABLE" for m in metrics) else "PARTIAL"
    previous = (
        await session.scalars(
            select(PipelineMetric)
            .where(
                PipelineMetric.pipeline_id == pipeline.id,
                PipelineMetric.sample_bucket.is_not(None),
                PipelineMetric.created_at >= now() - timedelta(seconds=3600),
            )
            .order_by(PipelineMetric.created_at.desc())
            .limit(120)
        )
    ).all()
    result.findings = analyse(
        result, [Observation.model_validate(p.values_json) for p in reversed(previous)]
    )
    return result


async def cleanup(session):
    ids = (
        select(PipelineMetric.id)
        .where(
            PipelineMetric.sample_bucket.is_not(None),
            PipelineMetric.created_at
            < now() - timedelta(days=get_settings().observability_retention_days),
        )
        .order_by(PipelineMetric.created_at)
        .limit(1000)
    )
    await session.execute(delete(PipelineMetric).where(PipelineMetric.id.in_(ids)))
