from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.auth import PERMISSIONS
from app.core.errors import DomainError
from app.schemas.data_quality import CheckInput, Comparison, Rule
from app.services.data_quality_engine import aggregate, evaluate, evaluation_window, schema_result
from app.services.data_quality_measurements import Reader, quote
from app.services.data_quality_types import (
    canonical,
    compatible,
    cross_provider_compatible,
    normalize_timestamp,
)

RULE = TypeAdapter(Rule)
NOW = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)


def config(**options):
    dataset = {
        "connection_id": str(uuid4()),
        "database": "dq",
        "schema_name": "public",
        "table": "items",
        "current_state_confirmed": True,
    }
    return CheckInput.model_validate(
        {
            "name": "quality",
            "source": dataset,
            "destination": {**dataset, "connection_id": str(uuid4())},
            "rules": [{"type": "row_count"}],
            **options,
        }
    )


def temporal(kind, **options):
    return RULE.validate_python(
        {
            "type": kind,
            "source_column": "updated_at",
            "destination_column": "updated_at",
            "semantics": "BUSINESS_UPDATED",
            "source_timezone": "UTC",
            "destination_timezone": "UTC",
            **options,
        }
    )


@pytest.mark.parametrize(
    "kind,source,destination,options,status",
    [
        ("row_count", 0, 0, {}, "PASS"),
        ("row_count", 4, 4, {}, "PASS"),
        ("row_count", 4, 3, {}, "FAIL"),
        ("row_count_diff", 100, 98, {"tolerance": 2}, "PASS"),
        ("row_count_diff", 100, 97, {"tolerance": 2}, "FAIL"),
        ("row_count_diff", 100, 98, {"mode": "PERCENTAGE", "tolerance": 2}, "PASS"),
        ("row_count_diff", 0, 0, {"mode": "PERCENTAGE"}, "PASS"),
        ("row_count_diff", 0, 1, {"mode": "PERCENTAGE", "tolerance": 100}, "FAIL"),
        ("row_count_diff", 1, 2, {"direction": "DESTINATION_BEHIND", "tolerance": 5}, "FAIL"),
    ],
)
def test_counts(kind, source, destination, options, status):
    rule = RULE.validate_python({"type": kind, **options})
    result = evaluate(rule, {"value": source}, {"value": destination}, NOW)
    assert result["quality"] == status
    assert result["signed_difference"] == source - destination
    if not source and destination:
        assert result["relative_difference_infinite"]
        assert result["percentage_difference"] is None


@pytest.mark.parametrize(
    "kind,source_age,dest_age,options,status,code",
    [
        ("last_updated", 1, 1, {}, "PASS", None),
        ("last_updated", 1, 3, {}, "FAIL", None),
        ("last_updated", None, None, {}, "INCONCLUSIVE", "EMPTY_OR_NULL_TIMESTAMP"),
        ("last_updated", 1, 2, {"tolerance_seconds": 2}, "PASS", None),
        ("freshness_delay", 1, 200, {}, "PASS", None),
        ("freshness_delay", 1, 400, {}, "FAIL", None),
        ("freshness_delay", 4, 1, {}, "INCONCLUSIVE", "DESTINATION_AHEAD_OR_CLOCK_SKEW"),
        ("freshness_delay", 1, -100, {}, "INCONCLUSIVE", "FUTURE_TIMESTAMP_OR_CLOCK_SKEW"),
        ("freshness_delay", 900, 1000, {}, "INCONCLUSIVE", "SOURCE_INACTIVE"),
        ("freshness_delay", 900, 1000, {"source_idle_policy": "EVALUATE"}, "PASS", None),
        ("data_age", 1, 1, {}, "PASS", None),
        ("data_age", 1, 900, {}, "FAIL", None),
        ("data_age", 900, 900, {}, "INCONCLUSIVE", "SOURCE_INACTIVE"),
        ("data_age", None, 900, {}, "INCONCLUSIVE", "SOURCE_ACTIVITY_UNKNOWN"),
        (
            "data_age",
            None,
            None,
            {"null_policy": "FAIL"},
            "INCONCLUSIVE",
            "SOURCE_ACTIVITY_UNKNOWN",
        ),
        ("data_age", 900, None, {"null_policy": "FAIL"}, "INCONCLUSIVE", "SOURCE_INACTIVE"),
        (
            "freshness_delay",
            None,
            None,
            {"null_policy": "FAIL"},
            "INCONCLUSIVE",
            "EMPTY_OR_NULL_TIMESTAMP",
        ),
        ("data_age", None, 900, {"activity_policy": "EXPECT_NEW_DATA"}, "FAIL", None),
        ("data_age", 1, -100, {}, "INCONCLUSIVE", "FUTURE_TIMESTAMP_OR_CLOCK_SKEW"),
    ],
)
def test_temporal(kind, source_age, dest_age, options, status, code):
    s = NOW - timedelta(seconds=source_age) if source_age is not None else None
    d = NOW - timedelta(seconds=dest_age) if dest_age is not None else None
    result = evaluate(temporal(kind, **options), {"value": s}, {"value": d}, NOW)
    assert result["quality"] == status
    assert result.get("error_code") == code
    if kind == "freshness_delay" and s and d:
        assert result["signed_delay_seconds"] == (s - d).total_seconds()


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "row_count_diff", "tolerance": -1},
        {"type": "row_count_diff", "tolerance": float("inf")},
        {"type": "row_count", "sql": "DROP TABLE x"},
        {"type": "custom_sql"},
        {"type": "last_updated", "destination_column": "ts", "semantics": "BUSINESS_UPDATED"},
    ],
)
def test_reject_invalid_rule(payload):
    with pytest.raises(ValidationError):
        RULE.validate_python(payload)


def test_defaults_and_windows():
    c = config()
    assert c.schedule.frequency_seconds == 600
    assert c.comparison.scope == "FULL_TABLE"
    assert evaluation_window(c, NOW) == {}
    c.comparison = Comparison(
        scope="TIME_WINDOW", source_time_column="ts", destination_time_column="ts"
    )
    w = evaluation_window(c, NOW)
    assert datetime.fromisoformat(w["end"]) == NOW - timedelta(seconds=120)
    assert datetime.fromisoformat(w["start"]) == NOW - timedelta(seconds=3720)
    with pytest.raises(ValidationError):
        config(schedule={"enabled": True, "frequency_seconds": 60})


def test_timestamp_normalization_requires_explicit_zone_and_rejects_dst():
    with pytest.raises(ValueError, match="NAIVE"):
        normalize_timestamp(datetime(2026, 1, 1), None)
    assert normalize_timestamp(datetime(2026, 1, 1, 7), "Asia/Bangkok").hour == 0
    for wall in (datetime(2026, 11, 1, 1, 30), datetime(2026, 3, 8, 2, 30)):
        with pytest.raises(ValueError):
            normalize_timestamp(wall, "America/New_York")


@pytest.mark.parametrize(
    "source,destination,expected",
    [
        ({"data_type": "integer"}, {"data_type": "bigint"}, True),
        ({"data_type": "bigint"}, {"data_type": "integer"}, False),
        ({"data_type": "int", "native_type": "int unsigned"}, {"data_type": "int"}, False),
        (
            {"data_type": "varchar", "character_maximum_length": 10},
            {"data_type": "varchar", "character_maximum_length": 20},
            True,
        ),
        (
            {"data_type": "numeric", "numeric_precision": 10, "numeric_scale": 2},
            {"data_type": "decimal", "numeric_precision": 12, "numeric_scale": 4},
            True,
        ),
        (
            {"data_type": "numeric", "numeric_precision": 10, "numeric_scale": 2},
            {"data_type": "decimal", "numeric_precision": 10, "numeric_scale": 4},
            False,
        ),
        (
            {"data_type": "timestamp with time zone"},
            {"data_type": "timestamp without time zone"},
            False,
        ),
        ({"data_type": "USER-DEFINED"}, {"data_type": "USER-DEFINED"}, None),
    ],
)
def test_types(source, destination, expected):
    assert compatible(canonical(source), canonical(destination)) is expected


def column(name="id", data_type="integer", **extra):
    return {
        "column_name": name,
        "data_type": data_type,
        "canonical": canonical({"data_type": data_type}),
        "ordinal_position": 1,
        "is_nullable": "NO",
        "primary_key_position": 1,
        **extra,
    }


def test_schema_options_and_unknown_types():
    rule = RULE.validate_python({"type": "schema_match"})
    assert schema_result(rule, [column()], [column(), column("extra")])["quality"] == "PASS"
    assert schema_result(rule, [column()], [column("other")])["quality"] == "FAIL"
    rule.column_mapping = {"id": "other"}
    assert schema_result(rule, [column()], [column("other")])["quality"] == "PASS"
    unknown = column("id", "USER-DEFINED")
    rule.column_mapping = {}
    assert schema_result(rule, [unknown], [unknown])["quality"] == "INCONCLUSIVE"


def test_schema_order_and_primary_key_respect_exclusions_without_ignoring_extra_keys():
    rule = RULE.validate_python(
        {
            "type": "schema_match",
            "compare_order": True,
            "compare_primary_key": True,
            "excluded_columns": ["ignored"],
        }
    )
    source = [
        column("ignored", ordinal_position=1, primary_key_position=None),
        column("id", ordinal_position=2),
        column("name", ordinal_position=3, primary_key_position=None),
    ]
    dest = [
        column("id", ordinal_position=1),
        column("name", ordinal_position=2, primary_key_position=None),
    ]
    assert schema_result(rule, source, dest)["quality"] == "PASS"
    dest.append(column("extra", ordinal_position=3, primary_key_position=2))
    result = schema_result(rule, source, dest)
    assert result["quality"] == "FAIL"
    assert any(d["reason"] == "PRIMARY_KEY_MISMATCH" for d in result["differences"])


def test_safe_predicates_and_identifier_quoting():
    db = Reader(None, "POSTGRESQL", 5)
    c = Comparison(source_filters=[{"column": "id", "operator": "eq", "value": "1 OR 1=1"}])
    with pytest.raises(DomainError):
        db.condition([column()], c, "source", {})
    c = Comparison(
        source_filters=[{"column": "text", "operator": "eq", "value": "';DROP TABLE x;--"}]
    )
    where, values = db.condition([column("text", "text")], c, "source", {})
    assert "DROP" not in where and values == ["';DROP TABLE x;--"]
    assert quote('a";DROP TABLE x;--', "POSTGRESQL") == '"a"";DROP TABLE x;--"'
    with pytest.raises(DomainError):
        quote("a\x00b", "MYSQL")


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["PASS"], "PASS"),
        (["PASS", "FAIL", "ERROR"], "FAIL"),
        (["PASS", "ERROR"], "ERROR"),
        (["PASS", "SKIPPED"], "INCONCLUSIVE"),
        ([], "INCONCLUSIVE"),
        (["NOT_SUPPORTED"], "INCONCLUSIVE"),
    ],
)
def test_aggregate(statuses, expected):
    assert aggregate(statuses) == expected


def test_rbac_permissions_are_module_scoped():
    assert "data_quality.read" in PERMISSIONS["Viewer"]
    assert "data_quality.run" not in PERMISSIONS["Viewer"]
    assert "data_quality.write" in PERMISSIONS["Ops"]
    assert "data_quality.admin" not in PERMISSIONS["Ops"]


@pytest.mark.parametrize(
    "source,destination,expected",
    [
        ({"data_type": "integer"}, {"data_type": "int"}, True),
        ({"data_type": "bigint"}, {"data_type": "int"}, False),
        ({"data_type": "integer"}, {"data_type": "bigint"}, True),
        ({"data_type": "bigint"}, {"data_type": "bigint", "native_type": "bigint unsigned"}, False),
        (
            {"data_type": "numeric", "numeric_precision": 10, "numeric_scale": 2},
            {"data_type": "decimal", "numeric_precision": 8, "numeric_scale": 2},
            False,
        ),
        (
            {"data_type": "numeric", "numeric_precision": 10, "numeric_scale": 2},
            {"data_type": "decimal", "numeric_precision": 10, "numeric_scale": 2},
            None,
        ),
        ({"data_type": "text"}, {"data_type": "longtext", "character_set_name": "utf8mb4"}, None),
        ({"data_type": "timestamp with time zone"}, {"data_type": "timestamp"}, False),
        ({"data_type": "date"}, {"data_type": "date"}, None),
        ({"data_type": "USER-DEFINED"}, {"data_type": "json"}, None),
    ],
)
def test_cross_provider_schema_contracts_are_conservative(source, destination, expected):
    assert cross_provider_compatible(canonical(source), canonical(destination)) is expected
