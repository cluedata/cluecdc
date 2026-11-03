"""Closed, bounded configuration: no SQL strings or arbitrary expressions."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Dataset(Strict):
    connection_id: UUID
    database: str = Field(min_length=1, max_length=128)
    schema_name: str = Field(min_length=1, max_length=128)
    table: str = Field(min_length=1, max_length=128)
    data_model: Literal["CURRENT_STATE"] = "CURRENT_STATE"
    current_state_confirmed: Literal[True]

    @field_validator("database", "schema_name", "table")
    @classmethod
    def identifier(cls, value: str):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Identifiers cannot contain control characters")
        return value


class Predicate(Strict):
    column: str = Field(min_length=1, max_length=128)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null"]
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def value_required(self):
        if self.operator not in {"is_null", "is_not_null"} and self.value is None:
            raise ValueError("A non-null predicate value is required")
        return self


class Comparison(Strict):
    scope: Literal["FULL_TABLE", "TIME_WINDOW"] = "FULL_TABLE"
    consistency: Literal["BEST_EFFORT"] = "BEST_EFFORT"
    period: Literal["ROLLING", "FIXED"] = "ROLLING"
    lookback_seconds: int = Field(default=3600, ge=60, le=2678400)
    settling_seconds: int = Field(default=120, ge=0, le=86400)
    source_time_column: str | None = Field(default=None, max_length=128)
    destination_time_column: str | None = Field(default=None, max_length=128)
    # Explicit timezones are necessary for naive timestamp columns.
    source_timezone: str | None = None
    destination_timezone: str | None = None
    window_semantics: Literal["BUSINESS_UPDATED", "INGESTION_TIME"] = "BUSINESS_UPDATED"
    fixed_start: datetime | None = None
    fixed_end: datetime | None = None
    source_filters: list[Predicate] = Field(default_factory=list, max_length=20)
    destination_filters: list[Predicate] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def window(self):
        for zone in (self.source_timezone, self.destination_timezone):
            validate_zone(zone)
        if self.scope == "TIME_WINDOW":
            if not self.source_time_column or not self.destination_time_column:
                raise ValueError("Time windows require temporal columns on both datasets")
            if self.period == "FIXED":
                if not self.fixed_start or not self.fixed_end:
                    raise ValueError("Fixed windows require both UTC boundaries")
                if not self.fixed_start.tzinfo or not self.fixed_end.tzinfo:
                    raise ValueError("Fixed window boundaries must include a timezone")
                if not 0 < (self.fixed_end - self.fixed_start).total_seconds() <= 2678400:
                    raise ValueError("Fixed windows must be positive and at most 31 days")
        return self


def validate_zone(value: str | None):
    if value:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use a known IANA timezone") from exc


class RuleBase(Strict):
    id: UUID | None = None
    enabled: bool = True


class RowCount(RuleBase):
    type: Literal["row_count"]


class RowCountDiff(RuleBase):
    type: Literal["row_count_diff"]
    mode: Literal["ABSOLUTE", "PERCENTAGE"] = "ABSOLUTE"
    tolerance: float = Field(default=0, ge=0, le=1e15)
    direction: Literal["BOTH", "DESTINATION_BEHIND", "DESTINATION_AHEAD"] = "BOTH"


class Temporal(RuleBase):
    source_column: str | None = Field(default=None, max_length=128)
    destination_column: str = Field(min_length=1, max_length=128)
    semantics: Literal["BUSINESS_UPDATED", "INGESTION_TIME"]
    source_timezone: str | None = None
    destination_timezone: str | None = None
    null_policy: Literal["INCONCLUSIVE", "FAIL"] = "INCONCLUSIVE"
    future_tolerance_seconds: int = Field(default=30, ge=0, le=3600)

    @model_validator(mode="after")
    def zones(self):
        validate_zone(self.source_timezone)
        validate_zone(self.destination_timezone)
        activity = getattr(self, "activity_policy", None)
        rule_type = getattr(self, "type", None)
        if rule_type != "data_age" or activity == "ONLY_WHEN_SOURCE_ACTIVE":
            if not self.source_column:
                raise ValueError("Source activity/timestamp column is required")
        if rule_type == "data_age" and activity == "ONLY_WHEN_SOURCE_ACTIVE":
            if self.semantics != "BUSINESS_UPDATED":
                raise ValueError("Source activity requires BUSINESS_UPDATED semantics")
        return self


class LastUpdated(Temporal):
    type: Literal["last_updated"]
    mode: Literal["EXACT", "WITHIN_TOLERANCE"] = "WITHIN_TOLERANCE"
    tolerance_seconds: float = Field(default=0, ge=0, le=2678400)


class FreshnessDelay(Temporal):
    type: Literal["freshness_delay"]
    max_delay_seconds: float = Field(default=300, ge=0, le=2678400)
    source_idle_policy: Literal["INCONCLUSIVE", "EVALUATE"] = "INCONCLUSIVE"
    activity_window_seconds: int = Field(default=600, ge=1, le=2678400)


class DataAge(Temporal):
    type: Literal["data_age"]
    max_age_seconds: float = Field(default=600, ge=0, le=2678400)
    activity_policy: Literal["ONLY_WHEN_SOURCE_ACTIVE", "EXPECT_NEW_DATA"] = (
        "ONLY_WHEN_SOURCE_ACTIVE"
    )
    activity_window_seconds: int = Field(default=600, ge=1, le=2678400)


class SchemaMatch(RuleBase):
    type: Literal["schema_match"]
    compare_names: bool = True
    compare_types: bool = True
    compare_nullability: bool = True
    compare_order: bool = False
    compare_primary_key: bool = False
    type_mode: Literal["EXACT", "COMPATIBLE"] = "COMPATIBLE"
    allow_extra_destination: bool = True
    allow_missing_source: bool = False
    column_mapping: dict[str, str] = Field(default_factory=dict, max_length=500)
    excluded_columns: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def meaningful(self):
        if not any(
            (
                self.compare_names,
                self.compare_types,
                self.compare_nullability,
                self.compare_order,
                self.compare_primary_key,
            )
        ):
            raise ValueError("Select at least one schema comparison")
        if len(set(self.column_mapping.values())) != len(self.column_mapping):
            raise ValueError("Column mappings must be one-to-one")
        return self


Rule = Annotated[
    RowCount | RowCountDiff | LastUpdated | FreshnessDelay | DataAge | SchemaMatch,
    Field(discriminator="type"),
]


class Schedule(Strict):
    enabled: bool = False
    frequency_seconds: Literal[60, 300, 600, 900, 1800, 3600, 21600, 86400] = 600
    timeout_seconds: int = Field(default=120, ge=5, le=180)
    display_timezone: str = "UTC"
    full_scan_acknowledged: bool = False
    retention_days: int = Field(default=90, ge=1, le=3650)

    @field_validator("display_timezone")
    @classmethod
    def timezone(cls, value):
        validate_zone(value)
        return value


class Alerting(Strict):
    channel_id: UUID | None = None
    enabled: bool | None = None
    severity: Literal["info", "warning", "critical"] = "warning"
    failure_threshold: int = Field(default=2, ge=1, le=100)
    cooldown_seconds: int = Field(default=900, ge=0, le=604800)
    send_recovery: bool = True
    on_fail: bool = True
    on_error: bool = True
    on_inconclusive: bool = False


class CheckInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    source: Dataset
    destination: Dataset
    pipeline_id: UUID | None = None
    enabled: bool = True
    comparison: Comparison = Field(default_factory=Comparison)
    rules: list[Rule] = Field(min_length=1, max_length=6)
    schedule: Schedule = Field(default_factory=Schedule)
    alerting: Alerting = Field(default_factory=Alerting)

    @model_validator(mode="after")
    def validate_check(self):
        if len({r.type for r in self.rules}) != len(self.rules):
            raise ValueError("Each of the six rule types may appear once per check")
        if not any(r.enabled for r in self.rules):
            raise ValueError("At least one rule must be enabled")
        if self.schedule.enabled and self.comparison.period == "FIXED":
            raise ValueError("Fixed windows are for manual execution only")
        if (
            self.schedule.enabled
            and self.schedule.frequency_seconds == 60
            and self.comparison.scope == "FULL_TABLE"
            and any(r.type in {"row_count", "row_count_diff"} for r in self.rules)
            and not self.schedule.full_scan_acknowledged
        ):
            raise ValueError("One-minute full counts require explicit query-cost acknowledgment")
        return self


class RunInput(Strict):
    request_key: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[\w-]+$")
