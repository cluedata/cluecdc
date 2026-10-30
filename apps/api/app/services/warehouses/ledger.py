from app.core.errors import DomainError
from app.models.entities import WarehouseBatchLedger, now

TRANSITIONS = {
    "CREATED": {"STAGING", "RETRYABLE_FAILED", "TERMINAL_FAILED"},
    "STAGING": {"STAGED", "RETRYABLE_FAILED", "TERMINAL_FAILED"},
    "STAGED": {"APPLYING", "RETRYABLE_FAILED", "TERMINAL_FAILED"},
    "APPLYING": {"APPLIED", "RETRYABLE_FAILED", "TERMINAL_FAILED"},
    "RETRYABLE_FAILED": {"STAGING", "APPLYING", "APPLIED"},
    "APPLIED": {"CHECKPOINTED"},
    "CHECKPOINTED": set(),
    "TERMINAL_FAILED": set(),
}


def transition(batch: WarehouseBatchLedger, status: str) -> None:
    if status not in TRANSITIONS.get(batch.status, set()):
        raise DomainError(
            "WAREHOUSE_BATCH_TRANSITION_INVALID",
            f"Batch cannot transition from {batch.status} to {status}",
            409,
        )
    batch.status = status
    if status == "APPLIED":
        batch.applied_at = now()
        batch.last_error_code = None
    elif status == "CHECKPOINTED":
        batch.checkpointed_at = now()


def fail(batch: WarehouseBatchLedger, code: str, *, retryable: bool = True) -> None:
    if batch.status in {"APPLIED", "CHECKPOINTED"}:
        raise DomainError("WAREHOUSE_BATCH_ALREADY_APPLIED", "An applied batch cannot fail", 409)
    batch.status = "RETRYABLE_FAILED" if retryable else "TERMINAL_FAILED"
    batch.retry_count = (batch.retry_count or 0) + 1
    batch.last_error_code = code[:80]


def checkpoint_ready(batch: WarehouseBatchLedger) -> bool:
    return batch.status == "APPLIED" and batch.applied_at is not None
