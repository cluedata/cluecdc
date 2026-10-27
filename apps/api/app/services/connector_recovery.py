from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.connect import KafkaConnectClient
from app.core.config import get_settings
from app.core.errors import DomainError
from app.models.entities import ConnectCluster, Connector, PipelineDestination, now
from app.services.connector_operations import claim, complete, owned, release
from app.workers.retry import retry_delay_seconds

log = structlog.get_logger()
HISTORY_LIMIT = 25


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _history(previous: dict[str, Any], event: dict[str, Any]) -> list[dict[str, Any]]:
    values = [item for item in previous.get("history", []) if isinstance(item, dict)]
    return [*values, event][-HISTORY_LIMIT:]


def _runtime(status: dict[str, Any], recovery: dict[str, Any]) -> dict[str, Any]:
    return {**status, "auto_recovery": recovery}


async def reconcile_failed_tasks(
    session: AsyncSession,
    *,
    delivery: PipelineDestination,
    connector: Connector,
    cluster: ConnectCluster,
    status: dict[str, Any],
) -> dict[str, Any]:
    """Schedule or execute one leased, bounded sink-task recovery attempt."""
    settings = get_settings()
    observed_at = now()
    observed_iso = _iso(observed_at)
    prior_runtime = connector.runtime_json if isinstance(connector.runtime_json, dict) else {}
    prior = (
        dict(prior_runtime.get("auto_recovery", {}))
        if isinstance(prior_runtime.get("auto_recovery"), dict)
        else {}
    )
    failed = [
        task
        for task in status.get("tasks", [])
        if str(task.get("state", "UNKNOWN")).upper() == "FAILED"
    ]
    if not failed:
        tasks = status.get("tasks", [])
        verified_running = bool(tasks) and all(
            str(task.get("state", "UNKNOWN")).upper() == "RUNNING" for task in tasks
        )
        if "error" in status or (prior.get("attempt_count", 0) and not verified_running):
            recovery = {
                **prior,
                "status": "OBSERVATION_UNAVAILABLE",
                "failed_task_ids": [],
            }
        elif prior.get("attempt_count", 0) or prior.get("status") in {
            "RESTARTING",
            "VERIFYING",
            "SCHEDULED",
            "EXHAUSTED",
        }:
            recovery = {
                **prior,
                "status": "RECOVERED",
                "failed_task_ids": [],
                "last_successful_recovery_at": observed_iso,
                "last_cycle_attempts": int(prior.get("attempt_count", 0)),
                "attempt_count": 0,
                "next_retry_at": None,
                "exhaustion_reason": None,
                "history": _history(
                    prior,
                    {
                        "at": observed_iso,
                        "outcome": "RECOVERY_VERIFIED",
                        "attempt": int(prior.get("attempt_count", 0)),
                    },
                ),
            }
        else:
            recovery = {**prior, "status": "IDLE", "failed_task_ids": []}
        return _runtime(status, recovery)

    task_ids = [int(task["id"]) for task in failed if isinstance(task.get("id"), int)]
    retryable = bool(task_ids) and all(task.get("retryable") is True for task in failed)
    new_cycle = prior.get("status") in {None, "IDLE", "RECOVERED"}
    attempt_count = 0 if new_cycle else int(prior.get("attempt_count", 0))
    recovery = {
        **prior,
        "status": "SCHEDULED",
        "failed_task_ids": task_ids,
        "failure_reason": str(failed[0].get("error") or "Kafka Connect task failed"),
        "failure_code": str(failed[0].get("error_code") or "CONNECT_TASK_FAILED"),
        "failure_category": str(failed[0].get("error_category") or "UNKNOWN"),
        "retryable": retryable,
        "first_failure_at": (
            observed_iso if new_cycle else prior.get("first_failure_at", observed_iso)
        ),
        "last_failure_at": observed_iso,
        "attempt_count": attempt_count,
        "max_attempts": settings.auto_recovery_max_attempts,
        "exhaustion_reason": None,
    }
    if not settings.auto_recovery_enabled:
        recovery.update(status="DISABLED", next_retry_at=None)
        return _runtime(status, recovery)
    if delivery.desired_state != "RUNNING" or connector.desired_state != "RUNNING":
        recovery.update(status="SUSPENDED", next_retry_at=None)
        return _runtime(status, recovery)
    if not retryable:
        recovery.update(
            status="NOT_RETRYABLE",
            next_retry_at=None,
            exhaustion_reason="Failure classification requires operator action",
        )
        return _runtime(status, recovery)
    if attempt_count >= settings.auto_recovery_max_attempts:
        recovery.update(
            status="EXHAUSTED",
            next_retry_at=None,
            exhaustion_reason=(
                f"Automatic recovery exhausted after {attempt_count} attempts; "
                "restart the failed task manually after resolving the destination"
            ),
        )
        return _runtime(status, recovery)

    eligible_at = _timestamp(prior.get("next_retry_at"))
    if eligible_at is not None and observed_at < eligible_at:
        recovery["next_retry_at"] = _iso(eligible_at)
        return _runtime(status, recovery)

    recovery.update(status="RESTARTING", next_retry_at=None)
    connector.runtime_json = _runtime(status, recovery)
    try:
        token, _ = await claim(session, connector.id, "auto-restart-task")
    except DomainError as error:
        if error.code != "CONNECTOR_OPERATION_ACTIVE":
            raise
        recovery.update(status="WAITING_FOR_OPERATION")
        return _runtime(status, recovery)

    await session.refresh(delivery)
    await session.refresh(connector)
    if delivery.desired_state != "RUNNING" or connector.desired_state != "RUNNING":
        recovery.update(status="SUSPENDED", next_retry_at=None)
        connector.runtime_json = _runtime(status, recovery)
        await release(session, connector.id, token)
        return connector.runtime_json

    accepted: list[int] = []
    failure_code: str | None = None
    client = KafkaConnectClient(cluster.base_url)
    for task_id in task_ids:
        try:
            await client.operate(connector.name, "restart-task", task_id)
            accepted.append(task_id)
        except DomainError as error:
            failure_code = error.code
            break
        except Exception:
            failure_code = "UNEXPECTED_RECOVERY_ERROR"
            break

    connector = await owned(session, connector.id, token)
    attempt_count += 1
    delay = retry_delay_seconds(
        attempt_count,
        settings.auto_recovery_base_delay_seconds,
        settings.auto_recovery_max_delay_seconds,
        settings.auto_recovery_jitter_ratio,
    )
    outcome = (
        "RESTART_REQUEST_ACCEPTED" if len(accepted) == len(task_ids) else "RESTART_REQUEST_FAILED"
    )
    recovery.update(
        status="VERIFYING" if outcome == "RESTART_REQUEST_ACCEPTED" else "SCHEDULED",
        attempt_count=attempt_count,
        last_attempt_at=observed_iso,
        next_retry_at=_iso(observed_at + timedelta(seconds=delay)),
        last_attempt_outcome=outcome,
        last_attempt_error_code=failure_code,
        history=_history(
            prior,
            {
                "at": observed_iso,
                "attempt": attempt_count,
                "task_ids": task_ids,
                "accepted_task_ids": accepted,
                "outcome": outcome,
                "error_code": failure_code,
            },
        ),
    )
    connector.runtime_json = _runtime(status, recovery)
    complete(connector, token)
    await session.commit()
    log.info(
        "connector_auto_recovery_attempted",
        connector_id=str(connector.id),
        delivery_id=str(delivery.id),
        task_ids=task_ids,
        attempt=attempt_count,
        outcome=outcome,
        error_code=failure_code,
    )
    return connector.runtime_json
