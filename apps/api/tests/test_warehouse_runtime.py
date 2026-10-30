import csv
import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.entities import KafkaCluster, PipelineDestination
from app.services.warehouses.format import serialize_history_csv
from app.services.warehouses.staged import process_staged_once


def event_record(offset: int = 7):
    return SimpleNamespace(
        topic="capture.public.orders",
        partition=0,
        offset=offset,
        timestamp=1234,
        key=b'{"tenant_id":1,"order_id":9}',
        value=(
            b'{"before":null,"after":{"tenant_id":1,"order_id":9},"source":{"lsn":42},"op":"c"}'
        ),
    )


def delivery() -> PipelineDestination:
    return PipelineDestination(
        id=uuid.uuid4(),
        pipeline_id=uuid.uuid4(),
        destination_id=uuid.uuid4(),
        name="Warehouse history",
        delivery_type="DATABASE",
        delivery_mode="insert",
        topic_mapping_json=[
            {
                "topic": "capture.public.orders",
                "schema_name": "analytics",
                "table_name": "orders_history",
            }
        ],
        configuration_json={"batch_size": 100, "batch_flush_interval_ms": 1000},
    )


class FakeConsumer:
    def __init__(self, records=None, commit_error=None):
        self.records = records or {}
        self.commit_error = commit_error
        self.commits = []

    async def start(self):
        return None

    async def stop(self):
        return None

    async def getmany(self, **kwargs):
        return self.records

    async def commit(self, offsets):
        if self.commit_error:
            raise self.commit_error
        self.commits.append(offsets)


def fake_session(*scalar_values):
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=list(scalar_values)),
        add=Mock(),
        commit=AsyncMock(),
    )
    return session


def classify(error: Exception) -> dict:
    return {"code": "WAREHOUSE_TRANSIENT", "retryable": True}


async def test_apply_marker_survives_crash_before_checkpoint_and_recovers(monkeypatch):
    current = delivery()
    kafka = KafkaCluster(
        name="Kafka", bootstrap_servers="kafka:29092", security_protocol="PLAINTEXT"
    )
    first_consumer = FakeConsumer(
        {"partition": [event_record()]}, commit_error=RuntimeError("commit unavailable")
    )
    monkeypatch.setattr(
        "app.services.warehouses.staged.AIOKafkaConsumer", lambda *args, **kwargs: first_consumer
    )
    session = fake_session(None, None)
    stage = AsyncMock(
        return_value={
            "data_uri": "s3://bucket/batch.manifest.json",
            "source_offsets": {"capture.public.orders:0": {"first": 7, "last": 7}},
        }
    )
    apply = AsyncMock()
    marker = AsyncMock(return_value=True)

    with pytest.raises(RuntimeError, match="commit unavailable"):
        await process_staged_once(
            session,
            current,
            kafka,
            {},
            provider="REDSHIFT",
            staging_prefix="cluecdc",
            stage=stage,
            apply=apply,
            marker_exists=marker,
            classify=classify,
        )
    batch = session.add.call_args.args[0]
    assert batch.status == "APPLIED"
    assert apply.await_count == 1

    recovery_consumer = FakeConsumer()
    monkeypatch.setattr(
        "app.services.warehouses.staged.AIOKafkaConsumer",
        lambda *args, **kwargs: recovery_consumer,
    )
    recovery_session = fake_session(batch)
    result = await process_staged_once(
        recovery_session,
        current,
        kafka,
        {},
        provider="REDSHIFT",
        staging_prefix="cluecdc",
        stage=stage,
        apply=apply,
        marker_exists=marker,
        classify=classify,
    )
    assert result["recovered"] is True
    assert batch.status == "CHECKPOINTED"
    assert apply.await_count == 1
    committed = next(iter(recovery_consumer.commits[0].values()))
    assert committed.offset == 8


async def test_staging_failure_is_retryable_and_does_not_commit(monkeypatch):
    consumer = FakeConsumer({"partition": [event_record()]})
    monkeypatch.setattr(
        "app.services.warehouses.staged.AIOKafkaConsumer", lambda *args, **kwargs: consumer
    )
    session = fake_session(None, None)
    stage = AsyncMock(side_effect=TimeoutError("S3 unavailable"))
    with pytest.raises(TimeoutError):
        await process_staged_once(
            session,
            delivery(),
            KafkaCluster(
                name="Kafka",
                bootstrap_servers="kafka:29092",
                security_protocol="PLAINTEXT",
            ),
            {},
            provider="REDSHIFT",
            staging_prefix="cluecdc",
            stage=stage,
            apply=AsyncMock(),
            marker_exists=AsyncMock(return_value=False),
            classify=classify,
        )
    batch = session.add.call_args.args[0]
    assert batch.status == "RETRYABLE_FAILED"
    assert batch.retry_count == 1
    assert consumer.commits == []


def test_history_csv_preserves_composite_key_as_unambiguous_json():
    event = {
        "topic": "orders",
        "partition": 2,
        "offset": 10,
        "timestamp": 99,
        "operation": "u",
        "key": {"tenant": "a:b", "id": 7},
        "before": {"value": "old"},
        "after": {"value": "new"},
        "source": {"lsn": 5},
    }
    row = next(csv.reader(io.StringIO(serialize_history_csv([event], "batch-1").decode())))
    assert row[5] == '{"tenant":"a:b","id":7}'
    assert row[9] == "batch-1"
