"""Six observational rules and live capability validation."""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import DomainError
from app.models.entities import Connection, Pipeline, PipelineDestination
from app.schemas.data_quality import CheckInput
from app.services.data_quality_measurements import reader
from app.services.data_quality_types import (
    compatible,
    cross_provider_compatible,
    normalize_timestamp,
)

SUPPORTED_PAIRS = frozenset(
    {
        ("POSTGRESQL", "POSTGRESQL"),
        ("MYSQL", "MYSQL"),
        ("POSTGRESQL", "MYSQL"),
    }
)
CROSS_PROVIDER_LIMITATIONS = [
    "PostgreSQL -> MySQL value comparisons normalize explicitly declared "
    "timestamp semantics/timezones.",
    "Cross-provider Schema Match certifies integer domains only; other unverified mappings are "
    "INCONCLUSIVE, while known narrowing/timezone contract changes FAIL.",
]

RULES = {
    "row_count": "Exact observed COUNT equality; matching counts do not prove row equality.",
    "row_count_diff": "Observed COUNT difference within an absolute or percentage threshold.",
    "last_updated": "Compare MAX timestamps with the same declared business semantics.",
    "freshness_delay": "Signed source MAX minus destination MAX; ahead values need investigation.",
    "data_age": "Age of destination MAX; source activity gates CDC failure by default.",
    "schema_match": "Current schema compatibility; does not use the evaluation period.",
}
LIMITATIONS = [
    "BEST_EFFORT: independent observations may race with concurrent writes.",
    "PASS only means the configured condition passed; it does not certify CDC completeness.",
    "Settling delay is not a verified replication checkpoint.",
]


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def evaluation_window(config: CheckInput, evaluation_time: datetime) -> dict:
    c = config.comparison
    if c.scope == "FULL_TABLE":
        return {}
    if c.period == "FIXED":
        assert c.fixed_start is not None and c.fixed_end is not None
        return {
            "start": c.fixed_start.astimezone(UTC).isoformat(),
            "end": c.fixed_end.astimezone(UTC).isoformat(),
        }
    end = aware(evaluation_time) - timedelta(seconds=c.settling_seconds)
    return {
        "start": (end - timedelta(seconds=c.lookback_seconds)).isoformat(),
        "end": end.isoformat(),
    }


async def connections(session, config: CheckInput) -> dict[str, Connection]:
    result = {}
    for side, capability in (("source", "SOURCE"), ("destination", "DESTINATION")):
        dataset = getattr(config, side)
        conn = await session.get(Connection, dataset.connection_id)
        if (
            not conn
            or conn.provider not in {"POSTGRESQL", "MYSQL"}
            or capability not in conn.capabilities_json
        ):
            raise DomainError(
                "DQ_PROVIDER_UNSUPPORTED",
                f"Select an authorized PostgreSQL/MySQL {side} connection",
                422,
            )
        if dataset.database != conn.database_name:
            raise DomainError("DQ_DATABASE_SCOPE", "Use the connection's registered database", 422)
        if conn.provider == "MYSQL" and dataset.schema_name != dataset.database:
            raise DomainError(
                "DQ_DATABASE_SCOPE", "MySQL schema must equal the registered database", 422
            )
        result[side] = conn
    if (result["source"].provider, result["destination"].provider) not in SUPPORTED_PAIRS:
        raise DomainError(
            "DQ_PAIR_UNVERIFIED", "This Source/Destination provider direction is unverified", 422
        )
    if config.pipeline_id:
        pipeline = await session.get(Pipeline, config.pipeline_id)
        if not pipeline or pipeline.source_id != config.source.connection_id:
            raise DomainError(
                "DQ_PIPELINE_MISMATCH", "Associated pipeline must use this Source", 422
            )
    # Explicitly reject known append-only/history deliveries rather than trusting a checkbox.
    from sqlalchemy import select

    deliveries = (
        await session.scalars(
            select(PipelineDestination).where(
                PipelineDestination.destination_id == config.destination.connection_id
            )
        )
    ).all()
    for delivery in deliveries:
        if delivery.delivery_mode.lower() in {"insert", "append", "append_only", "history"}:
            for mapping in delivery.topic_mapping_json:
                if (
                    mapping.get("destination_table", mapping.get("table_name"))
                    == config.destination.table
                    and mapping.get("destination_schema", mapping.get("schema_name"))
                    == config.destination.schema_name
                ):
                    raise DomainError(
                        "DQ_HISTORY_UNSUPPORTED", "Append-only history is not CURRENT_STATE", 422
                    )
    return result


def rule_capability(rule, schemas: dict, config: CheckInput, providers: dict) -> dict:
    reasons = []
    state = "SUPPORTED"
    if rule.type in {"last_updated", "freshness_delay", "data_age"}:
        for side in ("source", "destination"):
            name = getattr(rule, f"{side}_column")
            if name is None:
                continue
            col = next((c for c in schemas[side] if c["column_name"] == name), None)
            zone = getattr(rule, f"{side}_timezone")
            if not col or col["canonical"]["family"] != "timestamp":
                reasons.append(f"{side}: select an existing timestamp column")
                state = "REQUIRES_CONFIGURATION"
            elif not col["canonical"]["aware"] and not zone:
                reasons.append(f"{side}: declare the timezone of naive timestamps")
                state = "REQUIRES_CONFIGURATION"
            elif providers[side] == "MYSQL" and col["data_type"] == "timestamp" and zone != "UTC":
                reasons.append(f"{side}: MySQL TIMESTAMP is read in UTC; select UTC")
                state = "REQUIRES_CONFIGURATION"
    if rule.type == "schema_match":
        source_names = {c["column_name"] for c in schemas["source"]}
        dest_names = {c["column_name"] for c in schemas["destination"]}
        if (
            set(rule.column_mapping) - source_names
            or set(rule.column_mapping.values()) - dest_names
            or set(rule.excluded_columns) - source_names
        ):
            state = "REQUIRES_CONFIGURATION"
            reasons.append("Column mappings/exclusions must reference discovered columns")
        if any(c["canonical"]["family"] == "unknown" for cols in schemas.values() for c in cols):
            reasons.append("Unrecognized schema types will produce INCONCLUSIVE, never false PASS")
    return {
        "rule_id": str(rule.id) if rule.id else None,
        "rule_type": rule.type,
        "status": state,
        "reasons": reasons,
    }


async def inspect(
    session, config: CheckInput, evaluation_time: datetime, measure: bool = False
) -> tuple[dict, dict, dict]:
    endpoints = await connections(session, config)
    schemas: dict = {}
    observations: dict = {}
    window = evaluation_window(config, evaluation_time)
    for side, conn in endpoints.items():
        dataset = getattr(config, side)
        async with reader(session, conn, min(30, config.schedule.timeout_seconds)) as db:
            schema_started = time.monotonic()
            columns = await db.columns(dataset)
            schema_observation = {
                "value": len(columns),
                "observed_at": datetime.now(UTC).isoformat(),
                "duration_ms": round((time.monotonic() - schema_started) * 1000, 3),
            }
            schemas[side] = columns
            window_column = getattr(config.comparison, f"{side}_time_column")
            if conn.provider == "MYSQL" and config.comparison.scope == "TIME_WINDOW":
                for column in columns:
                    if (
                        column["column_name"] == window_column
                        and column["data_type"] == "timestamp"
                        and getattr(config.comparison, f"{side}_timezone") != "UTC"
                    ):
                        raise DomainError(
                            "DQ_WINDOW_TIMEZONE", "MySQL TIMESTAMP windows require UTC", 422
                        )
            # Validates allowlisted, typed predicates and window/timezone configuration.
            try:
                db.condition(columns, config.comparison, side, window)
            except ValueError as exc:
                raise DomainError(
                    "DQ_WINDOW_TIMEZONE", "Declare valid window timestamp timezones", 422
                ) from exc
            observations[side] = {"schema": schema_observation}
            if measure:
                names: set[str | None] = set()
                for rule in config.rules:
                    if not rule.enabled or rule.type == "schema_match":
                        continue
                    if rule.type in {"row_count", "row_count_diff"}:
                        names.add(None)
                    else:
                        name = getattr(rule, f"{side}_column")
                        if name:
                            names.add(name)
                for name in names:
                    # Avoid an invalid MAX query while still permitting other rules to run.
                    if name and not any(
                        c["column_name"] == name and c["canonical"]["family"] == "timestamp"
                        for c in columns
                    ):
                        continue
                    key = f"max:{name}" if name is not None else "count"
                    observations[side][key] = await db.measure(
                        dataset, columns, config.comparison, side, window, name
                    )
    capabilities = [
        rule_capability(r, schemas, config, {k: v.provider for k, v in endpoints.items()})
        for r in config.rules
        if r.enabled
    ]
    return (
        schemas,
        observations,
        {
            "rules": capabilities,
            "provider_pair": [endpoints["source"].provider, endpoints["destination"].provider],
            "window": window,
            "status": "SUPPORTED"
            if all(c["status"] == "SUPPORTED" for c in capabilities)
            else "REQUIRES_CONFIGURATION",
            "warnings": LIMITATIONS
            + (
                CROSS_PROVIDER_LIMITATIONS
                if endpoints["source"].provider != endpoints["destination"].provider
                else []
            )
            + (
                ["Full COUNT may scan the entire table."]
                if config.comparison.scope == "FULL_TABLE"
                else []
            ),
        },
    )


def aggregate(statuses: list[str]) -> str:
    for status in ("FAIL", "ERROR", "INCONCLUSIVE"):
        if status in statuses:
            return status
    return "PASS" if statuses and all(s == "PASS" for s in statuses) else "INCONCLUSIVE"


def schema_result(
    rule, source: list[dict], destination: list[dict], provider_pair: tuple[str, str] | None = None
) -> dict:
    source = [c for c in source if c["column_name"] not in rule.excluded_columns]
    dest = {c["column_name"]: c for c in destination}
    excluded_dest = {rule.column_mapping.get(n, n) for n in rule.excluded_columns}
    eligible_destination = [c for c in destination if c["column_name"] not in excluded_dest]
    differences = []
    unknown = False
    paired = []
    for index, col in enumerate(source):
        name = rule.column_mapping.get(col["column_name"], col["column_name"])
        other = (
            dest.get(name)
            if rule.compare_names
            else (eligible_destination[index] if index < len(eligible_destination) else None)
        )
        if other is None:
            if not rule.allow_missing_source:
                differences.append({"column": name, "reason": "MISSING_DESTINATION_COLUMN"})
            continue
        paired.append((col, other))
        if rule.compare_types:
            compare = (
                cross_provider_compatible
                if provider_pair == ("POSTGRESQL", "MYSQL")
                else compatible
            )
            match = compare(col["canonical"], other["canonical"], rule.type_mode == "EXACT")
            if match is None:
                unknown = True
                differences.append({"column": name, "reason": "UNSUPPORTED_TYPE"})
            elif not match:
                differences.append(
                    {
                        "column": name,
                        "reason": "TYPE_INCOMPATIBLE",
                        "source": col["canonical"],
                        "destination": other["canonical"],
                    }
                )
        if rule.compare_nullability and col["is_nullable"] != other["is_nullable"]:
            differences.append({"column": name, "reason": "NULLABILITY_MISMATCH"})
    paired_names = {d["column_name"] for _, d in paired}
    if rule.compare_order:
        source_order = [d["column_name"] for _, d in paired]
        destination_order = [
            d["column_name"]
            for _, d in sorted(paired, key=lambda pair: pair[1]["ordinal_position"])
        ]
        if source_order != destination_order:
            differences.append({"reason": "ORDER_MISMATCH"})
    if rule.compare_primary_key:
        mapped = {s["column_name"]: d["column_name"] for s, d in paired}
        source_key = [
            mapped.get(
                c["column_name"], rule.column_mapping.get(c["column_name"], c["column_name"])
            )
            for c in sorted(source, key=lambda c: c["primary_key_position"] or 0)
            if c["primary_key_position"]
        ]
        destination_key = [
            c["column_name"]
            for c in sorted(destination, key=lambda c: c["primary_key_position"] or 0)
            if c["primary_key_position"] and c["column_name"] not in excluded_dest
        ]
        if source_key != destination_key:
            differences.append(
                {
                    "reason": "PRIMARY_KEY_MISMATCH",
                    "source_key": source_key,
                    "destination_key": destination_key,
                }
            )
    if not rule.allow_extra_destination:
        for name in dest.keys() - paired_names - excluded_dest:
            differences.append({"column": name, "reason": "EXTRA_DESTINATION_COLUMN"})
    failures = [d for d in differences if d["reason"] != "UNSUPPORTED_TYPE"]
    return {
        "quality": "FAIL" if failures else "INCONCLUSIVE" if unknown or not paired else "PASS",
        "differences": differences[:500],
        "period_applied": False,
        "source_columns": len(source),
        "destination_columns": len(destination),
    }


def evaluate(rule, source: dict | None, destination: dict | None, evaluated_at: datetime) -> dict:
    evidence: dict = {"quality": "INCONCLUSIVE", "thresholds": rule.model_dump(mode="json")}
    s: Any = (source or {}).get("value")
    d: Any = (destination or {}).get("value")
    evidence.update(source=source, destination=destination)
    if rule.type in {"row_count", "row_count_diff"}:
        diff = s - d
        absolute = abs(diff)
        percentage = 100 * absolute / s if s else (0 if not d else None)
        evidence.update(
            signed_difference=diff,
            absolute_difference=absolute,
            percentage_difference=percentage,
            relative_difference_infinite=s == 0 and d != 0,
        )
        if rule.type == "row_count":
            passed = s == d
        else:
            in_direction = rule.direction == "BOTH" or (
                diff >= 0 if rule.direction == "DESTINATION_BEHIND" else diff <= 0
            )
            value = absolute if rule.mode == "ABSOLUTE" else percentage
            passed = in_direction and value is not None and value <= rule.tolerance
        evidence["quality"] = "PASS" if passed else "FAIL"
        return evidence
    try:
        if any(v is not None and not isinstance(v, datetime) for v in (s, d)):
            evidence["error_code"] = "INVALID_TIMESTAMP_VALUE"
            return evidence
        s = normalize_timestamp(s, rule.source_timezone)
        d = normalize_timestamp(d, rule.destination_timezone)
    except ValueError as exc:
        evidence["error_code"] = str(exc)
        return evidence
    evidence.update(source_latest=s, destination_latest=d)
    if rule.type == "freshness_delay" and s is not None and d is not None:
        evidence["signed_delay_seconds"] = (s - d).total_seconds()
    now = aware(evaluated_at)
    if any(v and (v - now).total_seconds() > rule.future_tolerance_seconds for v in (s, d)):
        evidence["error_code"] = "FUTURE_TIMESTAMP_OR_CLOCK_SKEW"
        return evidence
    if rule.type == "data_age" and rule.activity_policy == "ONLY_WHEN_SOURCE_ACTIVE":
        if s is None:
            evidence["error_code"] = "SOURCE_ACTIVITY_UNKNOWN"
            return evidence
        if (now - s).total_seconds() > rule.activity_window_seconds:
            evidence["error_code"] = "SOURCE_INACTIVE"
            return evidence
    if not d or (
        not s and (rule.type != "data_age" or rule.activity_policy == "ONLY_WHEN_SOURCE_ACTIVE")
    ):
        evidence["error_code"] = "EMPTY_OR_NULL_TIMESTAMP"
        evidence["quality"] = (
            "FAIL"
            if rule.null_policy == "FAIL" and rule.type != "freshness_delay"
            else "INCONCLUSIVE"
        )
        return evidence
    if rule.type == "last_updated":
        assert s is not None and d is not None
        difference = abs((s - d).total_seconds())
        limit = 0 if rule.mode == "EXACT" else rule.tolerance_seconds
        evidence.update(
            difference_seconds=difference, quality="PASS" if difference <= limit else "FAIL"
        )
    elif rule.type == "freshness_delay":
        assert s is not None and d is not None
        delay = (s - d).total_seconds()
        evidence["signed_delay_seconds"] = delay
        evidence["source_age_seconds"] = (now - s).total_seconds()
        evidence["destination_age_seconds"] = (now - d).total_seconds()
        if delay < 0:
            evidence["error_code"] = "DESTINATION_AHEAD_OR_CLOCK_SKEW"
        elif (
            rule.source_idle_policy == "INCONCLUSIVE"
            and (now - s).total_seconds() > rule.activity_window_seconds
        ):
            evidence["error_code"] = "SOURCE_INACTIVE"
        else:
            evidence["quality"] = "PASS" if delay <= rule.max_delay_seconds else "FAIL"
    else:
        assert d is not None
        age = (now - d).total_seconds()
        evidence["destination_age_seconds"] = age
        if (
            rule.activity_policy == "ONLY_WHEN_SOURCE_ACTIVE"
            and s is not None
            and (now - s).total_seconds() > rule.activity_window_seconds
        ):
            evidence["error_code"] = "SOURCE_INACTIVE"
        elif age < 0:
            evidence["error_code"] = "FUTURE_TIMESTAMP_OR_CLOCK_SKEW"
        else:
            evidence["quality"] = "PASS" if age <= rule.max_age_seconds else "FAIL"
    return evidence


async def execute(
    session, config: CheckInput, evaluation_time: datetime
) -> tuple[list[dict], dict]:
    schemas, observations, validation = await inspect(
        session, config, evaluation_time, measure=True
    )
    results = []
    for rule in config.rules:
        result: dict
        if not rule.enabled:
            result = {"quality": "SKIPPED"}
        elif (
            next(c for c in validation["rules"] if c["rule_type"] == rule.type)["status"]
            != "SUPPORTED"
        ):
            result = {"quality": "INCONCLUSIVE", "error_code": "CAPABILITY_REQUIRES_CONFIGURATION"}
        elif rule.type == "schema_match":
            result = schema_result(
                rule,
                schemas["source"],
                schemas["destination"],
                (validation["provider_pair"][0], validation["provider_pair"][1]),
            )
            result.update(
                source=observations["source"]["schema"],
                destination=observations["destination"]["schema"],
                thresholds=rule.model_dump(mode="json"),
            )
        else:
            s_key = (
                "count"
                if rule.type in {"row_count", "row_count_diff"}
                else f"max:{getattr(rule, 'source_column')}"
            )
            d_key = (
                "count"
                if rule.type in {"row_count", "row_count_diff"}
                else f"max:{getattr(rule, 'destination_column')}"
            )
            result = evaluate(
                rule,
                observations["source"].get(s_key),
                observations["destination"].get(d_key),
                datetime.now(UTC),
            )
        result.update(
            rule_id=str(rule.id),
            rule_type=rule.type,
            consistency="BEST_EFFORT",
            scope=config.comparison.scope if rule.type != "schema_match" else "CURRENT_SCHEMA",
            window=validation["window"] if rule.type != "schema_match" else {},
            provider_pair=validation["provider_pair"],
            limitations=LIMITATIONS
            + (
                CROSS_PROVIDER_LIMITATIONS
                if validation["provider_pair"] == ["POSTGRESQL", "MYSQL"]
                else []
            ),
        )
        results.append(result)
    return results, validation["window"]
