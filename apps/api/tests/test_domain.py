import re
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
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


def test_redaction_covers_nested_transport_and_private_key_secrets():
    value = redact(
        {
            "nested": [
                {"sasl.jaas.config": "user and password"},
                {"ssl.key.pem": "private key"},
                {"client_secret": "oauth secret"},
            ],
            "safe": "visible",
        }
    )
    assert value == {
        "nested": [
            {"sasl.jaas.config": "[REDACTED]"},
            {"ssl.key.pem": "[REDACTED]"},
            {"client_secret": "[REDACTED]"},
        ],
        "safe": "visible",
    }


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


def test_default_admin_is_development_only():
    with pytest.raises(ValidationError, match="public default Admin"):
        Settings(environment="production", bootstrap_default_admin=True)


def test_production_refuses_documented_example_secrets():
    with pytest.raises(ValidationError, match="example SECRET_ENCRYPTION_KEY"):
        production_settings(
            secret_encryption_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        )


def production_settings(**overrides):
    values = {
        "environment": "production",
        "auth_mode": "session",
        "bootstrap_default_admin": False,
        "database_url": "postgresql+asyncpg://cluecdc:unique-metadata-password@db/cluecdc",
        "secret_encryption_key": Fernet.generate_key().decode(),
        "connect_secret_token": "connect-token-0123456789-ABCDEF-unique",
        "session_secret": "session-secret-0123456789-ABCDEF-unique",
        "public_url": "https://cluecdc.example.com",
        "cors_origins": ["https://cluecdc.example.com"],
    }
    values.update(overrides)
    return Settings(**values)


def test_safe_production_configuration_is_accepted_and_normalized():
    settings = production_settings(
        public_url="https://CLUECDC.example.com:443/",
        cors_origins=["https://cluecdc.example.com"],
    )
    assert settings.public_url == "https://cluecdc.example.com"
    assert settings.cors_origins == ["https://cluecdc.example.com"]


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"public_url": "http://cluecdc.example.com"}, "PUBLIC_URL"),
        ({"public_url": "https://user@example.com"}, "PUBLIC_URL"),
        ({"cors_origins": []}, "CORS origin"),
        ({"cors_origins": ["*"]}, "wildcard"),
        ({"cors_origins": ["https://other.example.com"]}, "include PUBLIC_URL"),
        ({"session_secret": "a" * 40}, "SESSION_SECRET"),
        (
            {"database_url": "sqlite+aiosqlite:///metadata.db"},
            "Production DATABASE_URL",
        ),
        (
            {"database_url": "postgresql+asyncpg://cluecdc@db/cluecdc"},
            "Production DATABASE_URL",
        ),
    ],
)
def test_unsafe_production_configuration_is_rejected(overrides, error):
    with pytest.raises(ValidationError, match=error):
        production_settings(**overrides)


def test_production_security_secrets_must_be_unique():
    shared = "shared-secret-0123456789-ABCDEF-unique"
    with pytest.raises(ValidationError, match="must be unique"):
        production_settings(session_secret=shared, connect_secret_token=shared)


@pytest.mark.parametrize(
    "tokens",
    [
        "[]",
        '{"not-a-hash":{"actor":"operator","role":"Admin"}}',
        '{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":'
        '{"actor":"","role":"Admin"}}',
        '{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":'
        '{"actor":"operator","role":"Owner"}}',
    ],
)
def test_token_auth_configuration_has_a_safe_schema(tokens):
    with pytest.raises(ValidationError, match="AUTH_TOKENS_JSON"):
        Settings(auth_mode="token", auth_tokens_json=tokens)


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
