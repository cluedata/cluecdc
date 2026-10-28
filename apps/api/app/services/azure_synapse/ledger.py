from app.core.errors import DomainError
from app.models.entities import AzureSynapseBatchLedger, now

TRANSITIONS = {
    "PLANNED": {"STAGED", "FAILED"},
    "STAGED": {"COPIED", "FAILED"},
    "COPIED": {"APPLYING", "FAILED"},
    "APPLYING": {"APPLIED", "FAILED"},
    "FAILED": {"PLANNED", "STAGED", "COPIED", "APPLYING"},
    "APPLIED": set(),
}


def transition(batch: AzureSynapseBatchLedger, status: str) -> None:
    if status not in TRANSITIONS.get(batch.status, set()):
        raise DomainError(
            "AZURE_SYNAPSE_BATCH_TRANSITION_INVALID",
            f"Batch cannot transition from {batch.status} to {status}",
            409,
        )
    batch.status = status
    if status == "APPLIED":
        batch.applied_at = now()
        batch.last_error_code = None


def fail(batch: AzureSynapseBatchLedger, code: str) -> None:
    if batch.status == "APPLIED":
        raise DomainError(
            "AZURE_SYNAPSE_BATCH_ALREADY_APPLIED", "An applied batch cannot fail", 409
        )
    batch.status = "FAILED"
    batch.retry_count += 1
    batch.last_error_code = code[:80]


def checkpoint_ready(batch: AzureSynapseBatchLedger) -> bool:
    return batch.status == "APPLIED" and batch.applied_at is not None
