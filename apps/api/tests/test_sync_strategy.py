import uuid

import pytest

from app.core.errors import DomainError
from app.models.entities import SourceTable
from app.schemas.requests import PipelineInput
from app.services.debezium import message_key_columns
from app.services.sync_strategy import assess, key_candidates


def table(
    *,
    provider: str = "postgresql",
    primary: list[str] | None = None,
    columns: list[dict] | None = None,
    indexes: list[dict] | None = None,
    properties: dict | None = None,
) -> SourceTable:
    return SourceTable(
        source_id=uuid.uuid4(),
        schema_name="public" if provider == "postgresql" else "shop",
        table_name="cars",
        primary_key_columns=primary or [],
        columns_json=columns
        or [
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "code", "type": "text", "nullable": False},
        ],
        indexes_json=indexes or [],
        source_properties_json=properties or {},
        cdc_ready=True,
        cdc_status="READY",
        cdc_issues=[],
    )


def unique(columns: list[str], **overrides) -> dict:
    return {
        "name": "cars_code_key",
        "columns": columns,
        "unique": True,
        "primary": False,
        "valid": True,
        "partial": False,
        "expression": False,
        **overrides,
    }


def test_primary_key_preserves_current_state_behavior():
    result = assess(table(primary=["id"]), "postgresql")
    assert result["effective_key"]["mode"] == "PRIMARY_KEY"
    assert result["recommendation"]["sync_mode"] == "CURRENT_STATE"
    assert result["capabilities"]["incremental_snapshot"] == "SUPPORTED"


def test_postgres_safe_unique_key_requires_full_replica_identity():
    source = table(indexes=[unique(["code"])], properties={"replica_identity": "d"})
    result = assess(source, "postgresql")
    assert result["effective_key"]["mode"] == "UNIQUE_INDEX"
    assert result["capabilities"]["current_state"]["status"] == "REQUIRES_CONFIGURATION"
    assert "REPLICA IDENTITY FULL" in result["requirements"][0]["suggested_sql"]
    with pytest.raises(DomainError) as failure:
        assess(source, "postgresql", requested_sync_mode="CURRENT_STATE")
    assert failure.value.code == "SYNC_MODE_REQUIRES_CONFIGURATION"


def test_postgres_full_identity_enables_unique_key_current_state():
    result = assess(
        table(indexes=[unique(["code"])], properties={"replica_identity": "f"}),
        "postgresql",
    )
    assert result["recommendation"]["sync_mode"] == "CURRENT_STATE"
    assert result["capabilities"]["incremental_snapshot"] == "UNSUPPORTED"


@pytest.mark.parametrize(
    "index",
    [
        unique(["code"], partial=True),
        unique(["code"], expression=True),
        unique(["code"], valid=False),
    ],
)
def test_unsafe_unique_indexes_are_not_eligible(index):
    assert not key_candidates(table(indexes=[index]), "postgresql")[0]["eligible"]


def test_nullable_unique_index_is_not_eligible():
    source = table(
        columns=[{"name": "code", "type": "text", "nullable": True}],
        indexes=[unique(["code"])],
    )
    assert not key_candidates(source, "postgresql")[0]["eligible"]


def test_mysql_unique_key_requires_full_row_image():
    source = table(
        provider="mysql",
        indexes=[unique(["code"])],
        properties={"binlog_row_image": "MINIMAL"},
    )
    result = assess(source, "mysql")
    assert result["capabilities"]["current_state"]["status"] == "REQUIRES_CONFIGURATION"


def test_keyless_table_remains_available_for_event_log_or_append_only():
    result = assess(
        table(properties={"replica_identity": "f"}),
        "postgresql",
    )
    assert result["effective_key"]["mode"] == "NONE"
    assert result["recommendation"]["sync_mode"] == "CHANGE_EVENT_LOG"
    assert result["capabilities"]["current_state"]["status"] == "UNSUPPORTED"


def test_verified_only_custom_key_cannot_enable_current_state():
    source = table(properties={"replica_identity": "f"})

    with pytest.raises(DomainError) as failure:
        assess(
            source,
            "postgresql",
            requested_sync_mode="CURRENT_STATE",
            requested_key_mode="CUSTOM",
            requested_key_columns=["code"],
        )

    assert failure.value.code == "SYNC_MODE_UNSUPPORTED"


def test_message_key_columns_is_deterministic_and_merges_tables():
    pipeline = PipelineInput(
        name="capture",
        source_id=uuid.uuid4(),
        kafka_cluster_id=uuid.uuid4(),
        connect_cluster_id=uuid.uuid4(),
        topic_prefix="capture",
        tables=[
            {
                "schema_name": "public",
                "table_name": "orders+archive",
                "key_mode": "UNIQUE_INDEX",
                "key_columns": ["tenant_id", "external.id"],
            },
            {
                "schema_name": "public",
                "table_name": "cars",
                "key_mode": "UNIQUE_INDEX",
                "key_columns": ["code"],
            },
        ],
    )
    assert message_key_columns(pipeline.tables) == (
        r"public\.cars:code;public\.orders\+archive:tenant_id,external\.id"
    )
