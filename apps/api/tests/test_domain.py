import re
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import redact
from app.models.entities import Source
from app.schemas.requests import PipelineInput
from app.services.debezium import PostgresDebeziumConfigBuilder, derive_actual_state
from app.services.source import schema_diff


def pipeline(**kwargs):
    return PipelineInput(
        name="capture",
        source_id=uuid4(),
        kafka_cluster_id=uuid4(),
        connect_cluster_id=uuid4(),
        topic_prefix="commerce",
        tables=[{"schema_name": "public", "table_name": "orders.v2"}],
        **kwargs,
    )


def test_config_exact_table_regex_and_isolated_slot():
    source = Source(
        host="source", port=5432, username="cdc", database_name="commerce", ssl_enabled=False
    )
    config = PostgresDebeziumConfigBuilder().build(source, pipeline(), "private")
    pattern = config["table.include.list"]
    assert re.fullmatch(pattern, "public.orders.v2")
    assert not re.fullmatch(pattern, "public.ordersXv2")
    assert config["plugin.name"] == "pgoutput"
    assert config["publication.autocreate.mode"] == "filtered"
    assert len(config["slot.name"]) < 63
    assert redact(config)["database.password"] == "[REDACTED]"
    assert redact({"message": "password private"}, ["private"])["message"] == "password [REDACTED]"


@pytest.mark.parametrize(
    "status,expected",
    [
        ({"connector": {"state": "RUNNING"}, "tasks": [{"state": "RUNNING"}]}, "RUNNING"),
        ({"connector": {"state": "RUNNING"}, "tasks": [{"state": "FAILED"}]}, "DEGRADED"),
        ({"connector": {"state": "FAILED"}, "tasks": []}, "FAILED"),
        ({"connector": {"state": "RUNNING"}, "tasks": []}, "UNKNOWN"),
        ({"connector": {"state": "PAUSED"}, "tasks": []}, "PAUSED"),
    ],
)
def test_state_comes_from_runtime_tasks(status, expected):
    assert derive_actual_state(status) == expected


def test_queue_and_empty_tables_rejected():
    with pytest.raises(ValidationError):
        pipeline(max_batch_size=100, max_queue_size=100)
    with pytest.raises(ValidationError):
        PipelineInput(
            name="p",
            source_id=uuid4(),
            kafka_cluster_id=uuid4(),
            connect_cluster_id=uuid4(),
            topic_prefix="p",
            tables=[],
        )


def test_production_refuses_developer_auth():
    with pytest.raises(ValidationError):
        Settings(environment="production", auth_mode="developer")


def test_production_refuses_documented_example_secrets():
    with pytest.raises(ValidationError, match="example development secrets"):
        Settings(
            environment="production",
            auth_mode="token",
            auth_tokens_json='{"hash":{"actor":"operator","role":"Admin"}}',
            secret_encryption_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
            connect_secret_token="local-connect-token-change-before-production-0001",
        )


def test_schema_diff_is_deterministic():
    old = {
        "columns": [
            {"name": "a", "type": "integer", "nullable": True},
            {"name": "gone", "type": "text", "nullable": True},
        ],
        "primary_key": ["a"],
    }
    new = {
        "columns": [
            {"name": "a", "type": "bigint", "nullable": False},
            {"name": "new", "type": "text", "nullable": True},
        ],
        "primary_key": ["new"],
    }
    diffs = schema_diff(old, new)
    assert [d["change"] for d in diffs] == [
        "column_removed",
        "column_added",
        "type_changed",
        "nullable_changed",
        "primary_key_changed",
    ]
    assert [d["classification"] for d in diffs] == [
        "BREAKING",
        "NON_BREAKING",
        "POTENTIALLY_BREAKING",
        "BREAKING",
        "BREAKING",
    ]
