import uuid

import pytest

from app.core.errors import DomainError
from app.models.entities import Destination, Source
from app.providers import get_provider, provider_metadata
from app.providers.types import analyze_columns, destination_type, parse_type
from app.schemas.requests import DeliveryInput, PipelineInput, SourceInput
from app.services.debezium import MySQLDebeziumConfigBuilder
from app.services.destinations.adapter import MySQLDestinationAdapter


def mysql_source() -> Source:
    return Source(
        id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        name="mysql-source",
        type="mysql",
        environment="test",
        host="mysql",
        port=3306,
        database_name="shop",
        username="cdc",
        secret_ref=uuid.uuid4(),
        ssl_enabled=False,
        provider_options={"server_id": 540123456},
    )


def pipeline() -> PipelineInput:
    return PipelineInput(
        name="orders",
        source_id=uuid.uuid4(),
        kafka_cluster_id=uuid.uuid4(),
        connect_cluster_id=uuid.uuid4(),
        topic_prefix="prod_mysql_orders",
        snapshot_mode="initial",
        tables=[
            {"schema_name": "shop", "table_name": "customers"},
            {"schema_name": "shop", "table_name": "orders"},
        ],
    )


def test_provider_metadata_is_capability_driven():
    providers = {provider["type"]: provider for provider in provider_metadata()}
    assert providers["mysql"]["default_port"] == 3306
    assert providers["mysql"]["namespace_label"] == "Database"
    assert "INCREMENTAL_SNAPSHOT" in providers["mysql"]["capabilities"]
    assert get_provider("postgresql").metadata.namespace_label == "Schema"


def test_mysql_builder_uses_persisted_identity_and_multi_table_filters():
    config = MySQLDebeziumConfigBuilder().build(
        mysql_source(),
        pipeline(),
        "${cluecdc:secret:password}",
        connector_name="cluecdc-source-immutable",
        kafka_bootstrap_servers="kafka:29092",
        enable_signals=True,
    )
    assert config["connector.class"] == "io.debezium.connector.mysql.MySqlConnector"
    assert config["name"] == "cluecdc-source-immutable"
    assert config["database.server.id"] == "540123456"
    assert config["database.include.list"] == "shop"
    assert "shop\\.customers" in config["table.include.list"]
    assert "shop\\.orders" in config["table.include.list"]
    assert config["signal.data.collection"].startswith("shop.cluecdc_signal_")
    assert config["schema.history.internal.kafka.topic"].startswith("cluecdc.schema-history.")


def test_managed_debezium_properties_cannot_be_overridden():
    data = pipeline().model_dump()
    data["additional_debezium_properties"] = {"topic.prefix": "hijack"}
    with pytest.raises(ValueError):
        PipelineInput(**data)


@pytest.mark.parametrize(
    ("mysql_type", "postgres_type", "level"),
    [
        ("tinyint(1)", "boolean", "COMPATIBLE"),
        ("int unsigned", "bigint", "COMPATIBLE"),
        ("bigint unsigned", "numeric(20,0)", "COMPATIBLE"),
        ("decimal(30,6)", "numeric(30,6)", "COMPATIBLE"),
        ("json", "jsonb", "COMPATIBLE"),
        ("enum('new','paid')", "text", "WARNING"),
        ("geometry", None, "INCOMPATIBLE"),
    ],
)
def test_mysql_to_postgresql_type_matrix(mysql_type, postgres_type, level):
    result = destination_type(parse_type("mysql", mysql_type), "postgresql")
    assert result.destination_type == postgres_type
    assert result.compatibility == level


def test_postgresql_to_mysql_mapping_preserves_schema_metadata():
    columns = analyze_columns(
        [
            {"name": "id", "type": "bigint", "nullable": False, "ordinal": 1},
            {"name": "payload", "type": "jsonb", "nullable": True, "ordinal": 2},
            {"name": "amount", "type": "numeric(18,4)", "nullable": False, "ordinal": 3},
        ],
        "postgresql",
        "mysql",
    )
    assert [column["destination_type"] for column in columns] == [
        "bigint",
        "json",
        "decimal(18,4)",
    ]
    assert all(column["compatibility"] == "COMPATIBLE" for column in columns)


def test_mysql_source_options_are_validated():
    with pytest.raises(ValueError):
        SourceInput(
            name="source",
            type="mysql",
            host="mysql",
            port=3306,
            database_name="shop",
            username="cdc",
            provider_options={"server_id": 0},
        )


def test_mysql_destination_url_has_explicit_tls_mode():
    destination = Destination(
        name="target",
        type="mysql",
        environment="DEV",
        host="mysql-target",
        port=3306,
        database_name="analytics_mysql",
        username="delivery",
        secret_ref=uuid.uuid4(),
        ssl_enabled=True,
        provider_options={},
    )
    url = get_provider("mysql").build_destination_url(destination)
    assert url.startswith("jdbc:mysql://mysql-target:3306/analytics_mysql")
    assert "sslMode=VERIFY_IDENTITY" in url


async def test_mysql_destination_rejects_mapping_to_a_different_database():
    destination = Destination(
        name="target",
        type="mysql",
        environment="DEV",
        host="mysql-target",
        port=3306,
        database_name="analytics_mysql",
        username="delivery",
        secret_ref=uuid.uuid4(),
        ssl_enabled=False,
        provider_options={},
    )
    delivery = DeliveryInput(
        pipeline_id=uuid.uuid4(),
        mappings=[
            {
                "topic": "capture.public.customers",
                "schema_name": "public",
                "table_name": "customers",
            }
        ],
    )

    with pytest.raises(DomainError) as failure:
        await MySQLDestinationAdapter(destination, "password").validate(delivery, {})

    assert failure.value.code == "DESTINATION_DATABASE_MISMATCH"
    assert failure.value.details == {
        "database": "analytics_mysql",
        "mapping_database": "public",
    }
