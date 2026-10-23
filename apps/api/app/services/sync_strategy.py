"""Authoritative effective-key resolution and CDC capability assessment."""

from __future__ import annotations

from typing import Any

from app.core.errors import DomainError
from app.models.entities import PipelineTable, SourceTable
from app.providers.types import parse_type

SUPPORTED = "SUPPORTED"
REQUIRES_CONFIGURATION = "REQUIRES_CONFIGURATION"
UNSUPPORTED = "UNSUPPORTED"
UNKNOWN = "UNKNOWN"

SYNC_MODES = {"CURRENT_STATE", "APPEND_ONLY", "CHANGE_EVENT_LOG"}
KEY_MODES = {"PRIMARY_KEY", "UNIQUE_INDEX", "CUSTOM", "NONE"}


def _columns(table: SourceTable) -> dict[str, dict[str, Any]]:
    return {str(column.get("name")): column for column in table.columns_json}


def _compatible_key_type(provider: str, column: dict[str, Any]) -> bool:
    logical = parse_type(provider, str(column.get("type", "")))
    return logical.kind not in {"UNSUPPORTED", "SPATIAL"}


def _quote_postgres_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def key_candidates(table: SourceTable, provider: str) -> list[dict[str, Any]]:
    columns = _columns(table)
    candidates: list[dict[str, Any]] = []
    if table.primary_key_columns:
        candidates.append(
            {
                "mode": "PRIMARY_KEY",
                "columns": list(table.primary_key_columns),
                "constraint_name": None,
                "enforced": True,
                "verified": True,
                "eligible": True,
                "reason": "Database primary key",
            }
        )
    for index in table.indexes_json:
        names = list(index.get("columns") or [])
        unique = bool(index.get("unique"))
        valid = bool(index.get("valid", True))
        partial = bool(index.get("partial", False))
        expression = bool(index.get("expression", False))
        actual_columns = bool(names) and all(name in columns for name in names)
        not_null = actual_columns and all(not columns[name].get("nullable", True) for name in names)
        compatible = actual_columns and all(
            _compatible_key_type(provider, columns[name]) for name in names
        )
        eligible = unique and valid and not partial and not expression and not_null and compatible
        if unique and not index.get("primary", False):
            reasons = []
            if not valid:
                reasons.append("index is not valid")
            if partial:
                reasons.append("partial indexes are not stable row identities")
            if expression:
                reasons.append("expression indexes cannot be serialized as record keys")
            if not actual_columns:
                reasons.append("index does not contain only discovered table columns")
            if actual_columns and not not_null:
                reasons.append("all key columns must be NOT NULL")
            if actual_columns and not compatible:
                reasons.append("one or more key types are unsupported")
            candidates.append(
                {
                    "mode": "UNIQUE_INDEX",
                    "columns": names,
                    "constraint_name": index.get("name"),
                    "enforced": True,
                    "verified": eligible,
                    "eligible": eligible,
                    "replica_identity_eligible": bool(
                        provider == "postgresql"
                        and eligible
                        and index.get("replica_identity_eligible", False)
                    ),
                    "reason": "Database-enforced unique index" if eligible else "; ".join(reasons),
                }
            )
    candidates.sort(
        key=lambda candidate: (
            0 if candidate["mode"] == "PRIMARY_KEY" else 1,
            0 if candidate["eligible"] else 1,
            len(candidate["columns"]),
            ",".join(candidate["columns"]),
            str(candidate.get("constraint_name") or ""),
        )
    )
    return candidates


def _selected_candidate(
    candidates: list[dict[str, Any]],
    requested_mode: str | None,
    requested_columns: list[str] | None,
) -> dict[str, Any] | None:
    if requested_mode and requested_mode not in KEY_MODES:
        raise DomainError("INVALID_KEY_MODE", "Unsupported effective key mode", 422)
    if requested_mode == "NONE":
        return None
    columns = requested_columns or []
    if columns:
        match = next(
            (
                candidate
                for candidate in candidates
                if candidate["columns"] == columns
                and (not requested_mode or candidate["mode"] == requested_mode)
            ),
            None,
        )
        if match is None:
            return {
                "mode": requested_mode or "CUSTOM",
                "columns": columns,
                "constraint_name": None,
                "enforced": False,
                "verified": False,
                "eligible": False,
                "reason": "Custom key must be validated before use",
            }
        return match
    return next((candidate for candidate in candidates if candidate["eligible"]), None)


def assess(
    table: SourceTable,
    provider: str,
    *,
    requested_sync_mode: str | None = None,
    requested_key_mode: str | None = None,
    requested_key_columns: list[str] | None = None,
) -> dict[str, Any]:
    if requested_sync_mode and requested_sync_mode not in SYNC_MODES:
        raise DomainError("INVALID_SYNC_MODE", "Unsupported synchronization mode", 422)
    candidates = key_candidates(table, provider)
    key = _selected_candidate(candidates, requested_key_mode, requested_key_columns)
    properties = table.source_properties_json or {}
    replica_identity = properties.get("replica_identity")
    row_image = str(properties.get("binlog_row_image") or "").upper()
    requirements: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []

    current_state = UNSUPPORTED
    current_reason = "A stable database-enforced row identity is required"
    if key and key["eligible"]:
        if key["mode"] == "PRIMARY_KEY":
            current_state, current_reason = SUPPORTED, "Stable primary-key identity"
        elif provider == "postgresql" and replica_identity != "f":
            current_state = REQUIRES_CONFIGURATION
            current_reason = "Non-primary keys require REPLICA IDENTITY FULL"
            qualified = (
                f"{_quote_postgres_identifier(table.schema_name)}."
                f"{_quote_postgres_identifier(table.table_name)}"
            )
            requirements.append(
                {
                    "code": "POSTGRES_REPLICA_IDENTITY_FULL_REQUIRED",
                    "message": "Configure REPLICA IDENTITY FULL and rediscover metadata",
                    "suggested_sql": f"ALTER TABLE {qualified} REPLICA IDENTITY FULL;",
                }
            )
        elif provider == "mysql" and row_image != "FULL":
            current_state = REQUIRES_CONFIGURATION
            current_reason = "MySQL binlog_row_image must be FULL"
            requirements.append(
                {
                    "code": "MYSQL_FULL_ROW_IMAGE_REQUIRED",
                    "message": "Set binlog_row_image=FULL and rediscover metadata",
                }
            )
        else:
            current_state, current_reason = SUPPORTED, "Stable enforced unique identity"

    insert_capture = SUPPORTED if table.cdc_ready else UNSUPPORTED
    update_delete = SUPPORTED
    if provider == "postgresql" and not table.primary_key_columns and replica_identity != "f":
        update_delete = REQUIRES_CONFIGURATION
        warnings.append(
            {
                "code": "UPDATE_DELETE_IDENTITY_INCOMPLETE",
                "message": (
                    "UPDATE and DELETE events are not fully identifiable without "
                    "REPLICA IDENTITY FULL"
                ),
            }
        )
    elif provider == "mysql" and row_image != "FULL":
        update_delete = REQUIRES_CONFIGURATION if row_image else UNKNOWN

    change_event_log = (
        SUPPORTED
        if insert_capture == SUPPORTED and update_delete == SUPPORTED
        else REQUIRES_CONFIGURATION
        if insert_capture == SUPPORTED
        else UNSUPPORTED
    )
    incremental = SUPPORTED if key and key["mode"] == "PRIMARY_KEY" else UNSUPPORTED
    if key and key["mode"] != "PRIMARY_KEY":
        warnings.append(
            {
                "code": "INCREMENTAL_SNAPSHOT_UNVERIFIED",
                "message": "Incremental snapshots remain disabled for non-primary effective keys",
            }
        )

    recommendation = (
        "CURRENT_STATE"
        if current_state == SUPPORTED
        else "CHANGE_EVENT_LOG"
        if change_event_log == SUPPORTED
        else "APPEND_ONLY"
    )
    selected_mode = requested_sync_mode or recommendation
    selected_status = {
        "CURRENT_STATE": current_state,
        "CHANGE_EVENT_LOG": change_event_log,
        "APPEND_ONLY": insert_capture,
    }[selected_mode]
    if requested_sync_mode and selected_status != SUPPORTED:
        code = (
            "SYNC_MODE_REQUIRES_CONFIGURATION"
            if selected_status == REQUIRES_CONFIGURATION
            else "SYNC_MODE_UNSUPPORTED"
        )
        raise DomainError(
            code,
            f"{selected_mode} is not currently safe for {table.schema_name}.{table.table_name}",
            422,
            {"status": selected_status, "requirements": requirements, "warnings": warnings},
        )
    return {
        "table": f"{table.schema_name}.{table.table_name}",
        "effective_key": {
            "mode": key["mode"] if key else "NONE",
            "columns": list(key["columns"]) if key else [],
            "constraint_name": key.get("constraint_name") if key else None,
            "enforced": bool(key and key["enforced"]),
            "verified": bool(key and key["verified"]),
            "enforcement": "DATABASE_ENFORCED" if key and key["enforced"] else None,
        },
        "key_candidates": candidates,
        "capabilities": {
            "current_state": {"status": current_state, "reason": current_reason},
            "append_only": {
                "status": insert_capture,
                "reason": "INSERT capture does not require row identity",
            },
            "change_event_log": {
                "status": change_event_log,
                "reason": "Preserves source CDC envelopes when operations are identifiable",
            },
            "insert_capture": insert_capture,
            "update_capture": update_delete,
            "delete_capture": update_delete,
            "incremental_snapshot": incremental,
        },
        "recommendation": {
            "sync_mode": recommendation,
            "reason": current_reason
            if recommendation == "CURRENT_STATE"
            else "No safe current-state identity; preserve supported source events"
            if recommendation == "CHANGE_EVENT_LOG"
            else "Only insert capture is currently safe",
        },
        "selected_sync_mode": selected_mode,
        "requirements": requirements,
        "warnings": warnings,
    }


def pipeline_effective_key(table: PipelineTable) -> list[str]:
    """Resolve additive metadata while preserving v0.3 primary-key behavior."""
    if table.effective_key_columns is not None:
        return list(table.effective_key_columns)
    return list(table.primary_key_columns)
