import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.errors import DomainError
from app.models.entities import Connection
from app.schemas.requests import DeliveryInput
from app.services.clickhouse import ClickHouseDestinationAdapter
from app.services.destinations.providers import (
    BIGQUERY_CLASS,
    CLICKHOUSE_CLASS,
    delivery_provider,
)
from app.services.warehouses.schema import validate_fixed_history_schema


def input_for(schema: str) -> DeliveryInput:
    return DeliveryInput(
        pipeline_id=uuid.uuid4(),
        write_mode="insert",
        auto_evolve=False,
        delete_enabled=False,
        mappings=[
            {
                "topic": "capture.public.orders",
                "schema_name": schema,
                "table_name": "orders_history",
            }
        ],
    )


def test_clickhouse_connector_config_is_append_only_and_secret_referenced():
    reference = uuid.uuid4()
    connection = Connection(
        name="ClickHouse",
        category="DATABASE",
        provider="CLICKHOUSE",
        capabilities_json=["DESTINATION"],
        secret_ref=reference,
        config_json={
            "host": "clickhouse",
            "port": 8123,
            "database_name": "analytics",
            "username": "cluecdc",
            "ssl_enabled": False,
        },
    )
    config = delivery_provider(connection).build_config(
        connection, input_for("analytics"), {}, "delivery-clickhouse"
    )
    assert config["connector.class"] == CLICKHOUSE_CLASS
    assert config["exactlyOnce"] == "false"
    assert config["topic2TableMap"] == "capture.public.orders=orders_history"
    assert config["password"] == f"${{cluecdc:{reference}:password}}"
    assert config["transforms.history.type"].endswith("ClueWarehouseHistoryTransform")


def test_bigquery_connector_uses_apache_sink_storage_write_api_and_secret_reference():
    reference = uuid.uuid4()
    connection = Connection(
        name="BigQuery",
        category="DATABASE",
        provider="BIGQUERY",
        capabilities_json=["DESTINATION"],
        secret_ref=reference,
        config_json={
            "project_id": "cluecdc-analytics-123",
            "dataset_id": "cdc",
            "dataset_location": "US",
            "authentication": "SERVICE_ACCOUNT",
        },
    )
    config = delivery_provider(connection).build_config(
        connection, input_for("cdc"), {}, "delivery-bigquery"
    )
    assert config["connector.class"] == BIGQUERY_CLASS
    assert config["useStorageWriteApi"] == "true"
    assert config["topic2TableMap"] == "capture.public.orders:orders_history"
    assert config["keySource"] == "JSON"
    assert config["keyfile"] == f"${{cluecdc:{reference}:service_account_json}}"


def test_unverified_current_state_modes_are_rejected():
    connection = Connection(
        name="ClickHouse",
        category="DATABASE",
        provider="CLICKHOUSE",
        capabilities_json=["DESTINATION"],
        secret_ref=uuid.uuid4(),
        config_json={
            "host": "clickhouse",
            "port": 8123,
            "database_name": "analytics",
            "username": "cluecdc",
            "ssl_enabled": False,
        },
    )
    data = input_for("analytics").model_copy(update={"write_mode": "upsert"})
    with pytest.raises(DomainError) as error:
        delivery_provider(connection).build_config(connection, data, {}, "delivery")
    assert error.value.code == "CLICKHOUSE_CURRENT_STATE_NOT_VERIFIED"


async def test_clickhouse_existing_history_table_schema_is_validated():
    connection = Connection(
        name="ClickHouse",
        category="DATABASE",
        provider="CLICKHOUSE",
        capabilities_json=["DESTINATION"],
        config_json={
            "host": "clickhouse",
            "port": 8123,
            "database_name": "analytics",
            "username": "cluecdc",
            "ssl_enabled": False,
        },
    )
    adapter = ClickHouseDestinationAdapter(connection, "secret")
    adapter._query = AsyncMock(
        side_effect=[
            "1\n",
            json.dumps({"data": [{"name": "_clue_topic", "type": "String"}]}),
        ]
    )
    with pytest.raises(DomainError) as error:
        await adapter.validate(input_for("analytics"), {})
    assert error.value.code == "CLICKHOUSE_TABLE_SCHEMA_MISMATCH"


def test_fixed_warehouse_schema_rejects_incompatible_existing_column():
    with pytest.raises(DomainError) as error:
        validate_fixed_history_schema(
            {"_clue_offset": "STRING"},
            {"_clue_offset": "BIGINT"},
            provider="REDSHIFT",
            table="analytics.orders_history",
        )
    assert error.value.code == "REDSHIFT_TABLE_SCHEMA_MISMATCH"
    assert error.value.details["columns"]["_clue_offset"] == {
        "expected": "BIGINT",
        "actual": "STRING",
    }
