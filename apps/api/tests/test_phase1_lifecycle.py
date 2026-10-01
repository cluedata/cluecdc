from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.errors import DomainError
from app.models.entities import (
    ConnectCluster,
    Connector,
    Job,
    KafkaCluster,
    Pipeline,
    PipelineOperation,
    PipelineTable,
    SecretReference,
    Source,
    SourceTable,
)
from app.schemas.requests import (
    AddPipelineTablesInput,
    PipelineInput,
    RemovePipelineTableInput,
)
from app.services.debezium import PostgresDebeziumConfigBuilder, signal_table_name
from app.services.pipeline import (
    _apply_initial_snapshot_notifications,
    aggregate_pipeline_state,
)
from app.services.pipeline_tables import (
    _wait_connector_healthy,
    enqueue_add,
    enqueue_remove,
    retry_failed_operation,
    sync_snapshot_operations,
)
from app.services.publication import PostgresPublicationManager


def test_table_lifecycle_config_preserves_connector_identity():
    source = Source(
        host="source", port=5432, username="cdc", database_name="commerce", ssl_enabled=False
    )
    pipeline = PipelineInput(
        name="capture",
        source_id=uuid4(),
        kafka_cluster_id=uuid4(),
        connect_cluster_id=uuid4(),
        topic_prefix="commerce",
        tables=[{"schema_name": "public", "table_name": "customers"}],
    )
    builder = PostgresDebeziumConfigBuilder()
    before = builder.build(source, pipeline, "secret")
    pipeline.tables.append(pipeline.tables[0].model_copy(update={"table_name": "orders"}))
    after = builder.build(source, pipeline, "secret", enable_signals=True)
    assert before["slot.name"] == after["slot.name"]
    assert before["publication.name"] == after["publication.name"]
    assert "public\\.customers" in after["table.include.list"]
    assert "public\\.orders" in after["table.include.list"]
    assert signal_table_name("commerce") in after["signal.data.collection"]
    assert after["snapshot.mode"] == "initial"


@pytest.mark.parametrize(
    "capture,deliveries,expected",
    [
        ("RUNNING", ["RUNNING"], "RUNNING"),
        ("RUNNING", ["FAILED"], "DEGRADED"),
        ("RUNNING", ["UNKNOWN"], "DEGRADED"),
        ("FAILED", ["RUNNING"], "FAILED"),
    ],
)
def test_pipeline_health_aggregates_source_and_destination(capture, deliveries, expected):
    assert aggregate_pipeline_state(capture, deliveries) == expected


def test_initial_snapshot_state_uses_real_notifications():
    table = PipelineTable(
        pipeline_id=uuid4(),
        schema_name="public",
        table_name="customers",
        topic_name="commerce.public.customers",
        snapshot_status="PENDING",
    )
    _apply_initial_snapshot_notifications(
        [table],
        [
            {
                "aggregate_type": "Initial Snapshot",
                "type": "TABLE_SCAN_COMPLETED",
                "additional_data": {
                    "scanned_collection": "public.customers",
                    "total_rows_scanned": "42",
                    "status": "SUCCEEDED",
                },
            }
        ],
    )
    assert table.snapshot_status == "COMPLETED"
    assert table.snapshot_rows_processed == 42


def test_existing_pipeline_snapshot_uses_debezium_skipped_offset_signal():
    table = PipelineTable(
        pipeline_id=uuid4(),
        schema_name="public",
        table_name="customers",
        topic_name="commerce.public.customers",
        snapshot_status="PENDING",
    )

    _apply_initial_snapshot_notifications(
        [table],
        [{"aggregate_type": "Initial Snapshot", "type": "SKIPPED"}],
    )

    assert table.snapshot_status == "COMPLETED"
    assert table.snapshot_finished_at is not None


async def _metadata(db):
    secret = SecretReference(ciphertext="encrypted")
    kafka = KafkaCluster(name="kafka", bootstrap_servers="kafka:9092")
    db.add_all([secret, kafka])
    await db.flush()
    source = Source(
        name="source",
        type="postgresql",
        environment="test",
        host="source",
        port=5432,
        database_name="commerce",
        username="cdc",
        secret_ref=secret.id,
        ssl_enabled=False,
    )
    connect = ConnectCluster(
        name="connect",
        base_url="http://connect:8083",
        kafka_cluster_id=kafka.id,
    )
    db.add_all([source, connect])
    await db.flush()
    connector = Connector(
        name="cluecdc-commerce",
        connect_cluster_id=connect.id,
        connector_class="io.debezium.connector.postgresql.PostgresConnector",
        config_json={"slot.name": "stable", "publication.name": "stable"},
    )
    db.add(connector)
    await db.flush()
    pipeline = Pipeline(
        name="pipeline",
        source_id=source.id,
        kafka_cluster_id=kafka.id,
        connect_cluster_id=connect.id,
        connector_id=connector.id,
        topic_prefix="commerce",
        snapshot_mode="initial",
    )
    db.add(pipeline)
    await db.flush()
    return source, kafka, connector, pipeline


async def test_add_table_request_persists_operation_without_replacing_connector(db_factory):
    async with db_factory() as db:
        source, _, connector, pipeline = await _metadata(db)
        db.add(
            SourceTable(
                source_id=source.id,
                schema_name="public",
                table_name="orders",
                primary_key_columns=["id"],
                cdc_ready=True,
                cdc_status="READY",
            )
        )
        await db.flush()
        operations = await enqueue_add(
            db,
            pipeline.id,
            AddPipelineTablesInput(
                tables=[
                    {
                        "schema_name": "public",
                        "table_name": "orders",
                        "initial_data_strategy": "BACKFILL",
                    }
                ]
            ),
            "tester",
        )
        await db.commit()
        table = await db.scalar(select(PipelineTable))
        job = await db.scalar(select(Job).where(Job.resource_id == operations[0].id))
        await db.refresh(pipeline)
        assert table.cdc_status == "PENDING"
        assert table.snapshot_status == "PENDING"
        assert operations[0].status == "PENDING" and job is not None
        assert pipeline.connector_id == connector.id


async def test_remove_final_table_requires_pipeline_deletion(db_factory):
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        table = PipelineTable(
            pipeline_id=pipeline.id,
            schema_name="public",
            table_name="customers",
            topic_name="commerce.public.customers",
        )
        db.add(table)
        await db.flush()
        with pytest.raises(DomainError, match="final captured table"):
            await enqueue_remove(db, table.id, RemovePipelineTableInput(), "tester")


async def test_snapshot_notification_completes_durable_operation(db_factory, monkeypatch):
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        table = PipelineTable(
            pipeline_id=pipeline.id,
            schema_name="public",
            table_name="orders",
            topic_name="commerce.public.orders",
            snapshot_status="RUNNING",
            cdc_status="STREAMING",
        )
        db.add(table)
        await db.flush()
        operation = PipelineOperation(
            pipeline_id=pipeline.id,
            table_id=table.id,
            type="RESYNC_TABLE",
            status="WAITING",
            current_step="wait_snapshot",
            metadata_json={
                "signal_id": "signal-1",
                "notification_topic": "cluecdc.notifications.commerce",
                "steps": ["trigger_snapshot", "wait_snapshot", "verify_destination", "complete"],
                "completed_steps": ["trigger_snapshot"],
            },
        )
        db.add(operation)
        await db.commit()
        monkeypatch.setattr(
            "app.adapters.kafka.KafkaExplorer.notifications",
            AsyncMock(
                return_value=[
                    {
                        "id": "signal-1",
                        "type": "TABLE_SCAN_COMPLETED",
                        "additional_data": {
                            "status": "SUCCEEDED",
                            "total_rows_scanned": "150",
                        },
                    }
                ]
            ),
        )
        assert await sync_snapshot_operations(db) == 1
        await db.commit()
        assert operation.status == "SUCCEEDED"
        assert table.snapshot_status == "COMPLETED"
        assert table.snapshot_rows_processed == 150


async def test_connector_update_waits_for_stable_restarted_task(monkeypatch):
    config = AsyncMock(return_value={"table.include.list": "public\\.customers,public\\.orders"})
    status = AsyncMock(
        side_effect=[
            {"connector": {"state": "RUNNING"}, "tasks": [{"state": "RUNNING"}]},
            {"connector": {"state": "RUNNING"}, "tasks": [{"state": "RUNNING"}]},
        ]
    )
    sleeps = AsyncMock()
    monkeypatch.setattr("app.services.pipeline_tables.KafkaConnectClient.config", config)
    monkeypatch.setattr("app.services.pipeline_tables.KafkaConnectClient.status", status)
    monkeypatch.setattr("app.services.pipeline_tables.asyncio.sleep", sleeps)
    cluster = ConnectCluster(name="connect", base_url="http://connect:8083")

    await _wait_connector_healthy(
        cluster, "source", expected_table_filter="public\\.customers,public\\.orders"
    )

    assert status.await_count == 2
    assert sleeps.await_args_list[0].args == (2,)


async def test_retry_snapshot_operation_generates_a_new_signal(db_factory):
    async with db_factory() as db:
        _, _, _, pipeline = await _metadata(db)
        table = PipelineTable(
            pipeline_id=pipeline.id,
            schema_name="public",
            table_name="orders",
            topic_name="commerce.public.orders",
            snapshot_status="FAILED",
        )
        db.add(table)
        await db.flush()
        operation = PipelineOperation(
            pipeline_id=pipeline.id,
            table_id=table.id,
            type="RESYNC_TABLE",
            status="FAILED",
            current_step="wait_snapshot",
            metadata_json={
                "signal_id": "old-signal",
                "completed_steps": ["validate_source", "validate_connector", "trigger_snapshot"],
            },
        )
        db.add(operation)
        await db.flush()

        await retry_failed_operation(db, operation, "tester")

        assert operation.status == "PENDING"
        assert operation.current_step == "trigger_snapshot"
        assert "signal_id" not in operation.metadata_json
        assert "trigger_snapshot" not in operation.metadata_json["completed_steps"]
        assert table.snapshot_status == "PENDING"
        assert await db.scalar(select(Job).where(Job.resource_id == operation.id)) is not None


async def test_publication_manager_add_is_idempotent_and_validated():
    class Connection:
        def __init__(self):
            self.present = False
            self.executed = []

        async def fetchval(self, query, *args):
            if "FROM pg_publication WHERE" in query:
                return True
            if "FROM pg_publication_tables" in query:
                return self.present
            return True

        async def execute(self, query):
            self.executed.append(query)
            self.present = True

        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    connection = Connection()

    class Database:
        @asynccontextmanager
        async def connection(self):
            yield connection

    manager = PostgresPublicationManager(Database(), "cluecdc_publication")
    await manager.add_table("public", "orders")
    await manager.add_table("public", "orders")
    assert len(connection.executed) == 1
    assert (
        'ALTER PUBLICATION "cluecdc_publication" ADD TABLE "public"."orders"'
        in (connection.executed[0])
    )
