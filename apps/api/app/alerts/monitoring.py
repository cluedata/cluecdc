from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.classifier import ErrorClassifier
from app.alerts.domain import AlertEvent
from app.alerts.event_bus import alert_event_bus
from app.models.entities import Alert, Connector, Pipeline, PipelineDestination, Source, now

classifier = ErrorClassifier()


async def observe_database_connection(
    session: AsyncSession,
    *,
    kind: str,
    identifier: str,
    name: str,
    database_type: str,
    firing: bool,
    message: str,
) -> None:
    fallback = "SOURCE_CONNECTION_FAILED" if kind == "source" else "DESTINATION_CONNECTION_FAILED"
    classified = classifier.classify(message, fallback)
    if kind == "destination" and classified == "SOURCE_AUTH_FAILED":
        classified = "DESTINATION_AUTH_FAILED"
    if kind == "destination" and classified == "SOURCE_DATABASE_UNAVAILABLE":
        classified = "DESTINATION_CONNECTION_FAILED"
    event_types = [classified]
    if not firing:
        event_types = (
            [
                "SOURCE_CONNECTION_FAILED",
                "SOURCE_DATABASE_UNAVAILABLE",
                "SOURCE_AUTH_FAILED",
                "CDC_REPLICATION_SLOT_ERROR",
                "CDC_BINLOG_ERROR",
                "CDC_WAL_ERROR",
                "CDC_PERMISSION_ERROR",
            ]
            if kind == "source"
            else [
                "DESTINATION_CONNECTION_FAILED",
                "DESTINATION_AUTH_FAILED",
                "DESTINATION_WRITE_FAILED",
                "DESTINATION_SCHEMA_ERROR",
            ]
        )
    for event_type in event_types:
        await _emit(
            session,
            AlertEvent(
                event_type=event_type,
                severity="critical",
                status="firing" if firing else "resolved",
                source_type=kind,
                source_id=identifier,
                source_name=name,
                pipeline_id=None,
                pipeline_name=None,
                component=f"{kind}/{database_type}",
                message=message,
                details={
                    "database_type": database_type,
                    "raw_error": message if firing else None,
                },
            ),
        )


async def observe_connector(
    session: AsyncSession,
    *,
    pipeline: Pipeline,
    connector: Connector,
    status: dict[str, Any],
    state: str,
    desired_state: str,
    delivery: PipelineDestination | None = None,
) -> None:
    """Translate one Kafka Connect observation into normalized firing/recovery events."""
    source_type = "destination" if delivery else "connector"
    delivery_source_id = delivery.destination_id if delivery else pipeline.source_id
    source_id = str(delivery_source_id)
    source = await session.get(Source, pipeline.source_id) if not delivery else None
    source_name = delivery.name if delivery else (source.name if source else connector.name)
    component = "destination" if delivery else "kafka-connect"
    common: dict[str, Any] = {
        "source_type": source_type,
        "source_id": source_id,
        "source_name": source_name,
        "pipeline_id": pipeline.id,
        "pipeline_name": pipeline.name,
        "component": component,
    }

    unavailable = "error" in status
    await _emit(
        session,
        AlertEvent(
            event_type="KAFKA_CONNECT_UNAVAILABLE",
            severity="critical",
            status="firing" if unavailable else "resolved",
            message=(status.get("error") or {}).get("message", "Kafka Connect is reachable"),
            details={"connector": connector.name, **(status.get("error") or {})},
            **common,
        ),
    )

    recent_failures = await session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(
            Alert.event_type == "CONNECTOR_FAILED",
            Alert.source_id == source_id,
            Alert.created_at >= now() - timedelta(minutes=10),
        )
    )
    restart_loop = (recent_failures or 0) >= 3
    await _emit(
        session,
        AlertEvent(
            event_type="CONNECTOR_RESTART_LOOP",
            severity="warning",
            status="firing" if restart_loop else "resolved",
            message="Connector failed at least three times in ten minutes",
            details={
                "connector": connector.name,
                "failure_count": recent_failures or 0,
                "window_minutes": 10,
            },
            **common,
        ),
    )

    connector_state = str((status.get("connector") or {}).get("state", state)).upper()
    connector_events = {
        "FAILED": "CONNECTOR_FAILED",
        "UNASSIGNED": "CONNECTOR_UNASSIGNED",
    }
    for observed_state, event_type in connector_events.items():
        firing = connector_state == observed_state
        await _emit(
            session,
            AlertEvent(
                event_type=event_type,
                severity="critical" if observed_state == "FAILED" else "warning",
                status="firing" if firing else "resolved",
                message=(status.get("connector") or {}).get("error")
                or f"Connector {connector_state.lower()}",
                details={"connector": connector.name, "state": connector_state},
                **common,
            ),
        )
    if not delivery:
        await _emit(
            session,
            AlertEvent(
                event_type="CDC_CONNECTOR_FAILED",
                severity="critical",
                status="firing" if connector_state == "FAILED" else "resolved",
                message=(status.get("connector") or {}).get("error")
                or f"CDC connector {connector_state.lower()}",
                details={"connector": connector.name, "state": connector_state},
                **common,
            ),
        )
    paused_unexpected = connector_state == "PAUSED" and desired_state != "PAUSED"
    await _emit(
        session,
        AlertEvent(
            event_type="CONNECTOR_PAUSED",
            severity="warning",
            status="firing" if paused_unexpected else "resolved",
            message="Connector paused outside the requested lifecycle state",
            details={"connector": connector.name, "state": connector_state},
            **common,
        ),
    )

    for task in status.get("tasks") or []:
        task_failed = str(task.get("state", "UNKNOWN")).upper() == "FAILED"
        raw_error = str(task.get("error") or "Kafka Connect task failed")
        classified = classifier.classify(raw_error, "CONNECT_TASK_FAILED")
        if delivery and classified == "SOURCE_AUTH_FAILED":
            classified = "DESTINATION_AUTH_FAILED"
        if delivery and classified == "SOURCE_DATABASE_UNAVAILABLE":
            classified = "DESTINATION_CONNECTION_FAILED"
        if not delivery and classified.startswith("DESTINATION_"):
            classified = "CONNECT_TASK_FAILED"
        event_types = list(dict.fromkeys(["CONNECT_TASK_FAILED", classified]))
        if not task_failed:
            event_types = [
                "CONNECT_TASK_FAILED",
                "CDC_REPLICATION_SLOT_ERROR",
                "CDC_BINLOG_ERROR",
                "CDC_WAL_ERROR",
                "CDC_PERMISSION_ERROR",
                "SOURCE_AUTH_FAILED",
                "SOURCE_DATABASE_UNAVAILABLE",
                "DESTINATION_CONNECTION_FAILED",
                "DESTINATION_SCHEMA_ERROR",
                "DESTINATION_AUTH_FAILED",
                "DESTINATION_WRITE_FAILED",
            ]
        for event_type in event_types:
            await _emit(
                session,
                AlertEvent(
                    event_type=event_type,
                    severity="critical",
                    status="firing" if task_failed else "resolved",
                    message=raw_error,
                    details={
                        "connector": connector.name,
                        "task_id": task.get("id"),
                        "state": task.get("state"),
                        "error": raw_error if task_failed else None,
                    },
                    **common,
                ),
            )

    pipeline_event = "PIPELINE_FAILED" if state in {"FAILED", "UNKNOWN"} else "PIPELINE_DEGRADED"
    affected = state in {"FAILED", "UNKNOWN", "DEGRADED"}
    for event_type in ("PIPELINE_FAILED", "PIPELINE_DEGRADED"):
        firing = affected and event_type == pipeline_event
        await _emit(
            session,
            AlertEvent(
                event_type=event_type,
                severity="critical" if event_type == "PIPELINE_FAILED" else "warning",
                status="firing" if firing else "resolved",
                message=f"Pipeline runtime state is {state}",
                details={"connector": connector.name, "state": state},
                **common,
            ),
        )

    if delivery:
        destination_failed = state in {"FAILED", "UNKNOWN"}
        await _emit(
            session,
            AlertEvent(
                event_type="DESTINATION_CONNECTOR_FAILED",
                severity="critical",
                status="firing" if destination_failed else "resolved",
                message=f"Destination connector runtime state is {state}",
                details={"connector": connector.name, "state": state},
                **common,
            ),
        )


async def _emit(session: AsyncSession, event: AlertEvent) -> None:
    await alert_event_bus.emit(session, event)
