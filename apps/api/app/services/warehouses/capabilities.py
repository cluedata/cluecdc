from copy import deepcopy

_CAPABILITIES: dict[str, dict] = {
    "AZURE_SYNAPSE": {
        "append_only": True,
        "current_state_cdc": False,
        "initial_snapshot": True,
        "upsert": False,
        "delete": False,
        "composite_key": False,
        "schema_evolution": "VALIDATE",
        "direct_streaming": False,
        "staged_batch": True,
        "runtime": "IMPLEMENTED",
        "mock_verification": "PASS",
        "destination_verification": "NOT_RUN",
        "production_ready": False,
        "limitations": [
            "Append-only staged COPY requires opt-in Azure cloud validation.",
            "Current-state CDC is disabled until Dedicated SQL Pool apply semantics are verified.",
        ],
    },
    "CLICKHOUSE": {
        "append_only": True,
        "current_state_cdc": False,
        "initial_snapshot": True,
        "upsert": False,
        "delete": False,
        "composite_key": False,
        "schema_evolution": "VALIDATE",
        "direct_streaming": True,
        "staged_batch": False,
        "runtime": "IMPLEMENTED",
        "mock_verification": "PASS",
        "destination_verification": "PASS",
        "production_ready": False,
        "limitations": [
            "Append-only history is available through the pinned ClickHouse Kafka Connect sink.",
            "Delivery is at-least-once unless operators separately provision and validate "
            "KeeperMap.",
            "Local sink rows, restart, and outage recovery passed; full PostgreSQL/MySQL "
            "capture matrices did not run.",
            "ReplacingMergeTree current-state mode remains disabled until replay and delete "
            "behavior pass container E2E tests.",
        ],
    },
    "BIGQUERY": {
        "append_only": True,
        "current_state_cdc": False,
        "initial_snapshot": True,
        "upsert": False,
        "delete": False,
        "composite_key": False,
        "schema_evolution": "VALIDATE",
        "direct_streaming": True,
        "staged_batch": False,
        "runtime": "IMPLEMENTED",
        "mock_verification": "PASS",
        "destination_verification": "NOT_RUN",
        "production_ready": False,
        "limitations": [
            "Append-only history uses the Apache-2.0 Aiven BigQuery connector v2.15.0.",
            "Current-state CDC remains disabled until replay and delete E2E validation.",
        ],
    },
    "REDSHIFT": {
        "append_only": True,
        "current_state_cdc": False,
        "initial_snapshot": True,
        "upsert": False,
        "delete": False,
        "composite_key": False,
        "schema_evolution": "VALIDATE",
        "direct_streaming": False,
        "staged_batch": True,
        "runtime": "IMPLEMENTED",
        "mock_verification": "PASS",
        "destination_verification": "NOT_RUN",
        "production_ready": False,
        "limitations": [
            "Append-only history uses S3 manifests, Redshift COPY, and destination markers.",
            "Current-state MERGE remains disabled until real Redshift recovery tests pass.",
        ],
    },
}


def capabilities(provider: str) -> dict | None:
    """Return an immutable-by-convention capability snapshot for API consumers."""
    value = _CAPABILITIES.get(provider)
    return deepcopy(value) if value is not None else None


def runtime_available(provider: str, mode: str) -> bool:
    value = _CAPABILITIES.get(provider, {})
    return bool(value.get("append_only" if mode == "insert" else "current_state_cdc"))
