import csv
import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.entities import KafkaCluster, PipelineDestination, WarehouseBatchLedger, now
from app.services.azure_synapse.planner import plan_batch
from app.services.redshift_runtime import _count
from app.services.warehouses.format import serialize_history_csv
from app.services.warehouses.kafka import normalize_event
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

    def assignment(self):
        from aiokafka.structs import TopicPartition

        return {TopicPartition("capture.public.orders", 0)}

    async def beginning_offsets(self, partitions):
        return dict.fromkeys(partitions, 0)

    def seek(self, partition, offset):
        self.seek_offset = offset


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


def test_redshift_count_requires_a_result_row():
    assert _count([1]) == 1
    with pytest.raises(RuntimeError, match="COUNT query returned no row"):
        _count(None)


@pytest.mark.parametrize("provider", ["REDSHIFT", "AZURE_SYNAPSE"])
@pytest.mark.parametrize("state", ["CREATED", "STAGING", "STAGED", "APPLYING", "RETRYABLE_FAILED"])
async def test_pending_batch_recovers_original_range_despite_changed_poll(
    monkeypatch, provider, state
):
    current = delivery()
    plan = plan_batch(str(current.id), [normalize_event(event_record(7))], "cluecdc")
    manifest = (
        {"data_uri": "s3://fixture/original.csv"}
        if state in {"STAGED", "APPLYING"}
        else {"record_count": 1}
    )
    batch = WarehouseBatchLedger(
        delivery_id=current.id,
        batch_id=plan.batch_id,
        source_offsets_json=plan.source_offsets,
        staging_manifest_json=manifest,
        status=state,
    )
    from aiokafka.structs import TopicPartition

    consumer = FakeConsumer(
        {TopicPartition("capture.public.orders", 0): [event_record(7), event_record(8)]}
    )
    monkeypatch.setattr("app.services.warehouses.staged.AIOKafkaConsumer", lambda *a, **k: consumer)
    stage = AsyncMock(return_value={"data_uri": "s3://fixture/original.csv"})
    apply = AsyncMock()
    result = await process_staged_once(
        fake_session(batch),
        current,
        KafkaCluster(name="Kafka", bootstrap_servers="kafka:29092", security_protocol="PLAINTEXT"),
        {},
        provider=provider,
        staging_prefix="cluecdc",
        stage=stage,
        apply=apply,
        marker_exists=AsyncMock(return_value=True),
        classify=classify,
    )
    assert result["batch_id"] == plan.batch_id
    assert batch.status == "CHECKPOINTED"
    assert next(iter(consumer.commits[0].values())).offset == 8
    apply.assert_awaited_once_with(plan.batch_id, "s3://fixture/original.csv")
    if manifest.get("data_uri"):
        stage.assert_not_awaited()
    else:
        staged_csv = stage.await_args.args[1].decode()
        assert len(list(csv.reader(io.StringIO(staged_csv)))) == 1
        assert consumer.seek_offset == 7


async def test_applied_missing_marker_blocks_checkpoint_without_erasing_state(monkeypatch):
    current = delivery()
    batch = WarehouseBatchLedger(
        delivery_id=current.id,
        batch_id="original",
        source_offsets_json={},
        staging_manifest_json={},
        status="APPLIED",
        applied_at=now(),
    )
    consumer = FakeConsumer()
    monkeypatch.setattr("app.services.warehouses.staged.AIOKafkaConsumer", lambda *a, **k: consumer)
    with pytest.raises(Exception, match="Applied ledger batch has no destination marker"):
        await process_staged_once(
            fake_session(batch),
            current,
            KafkaCluster(
                name="Kafka", bootstrap_servers="kafka:29092", security_protocol="PLAINTEXT"
            ),
            {},
            provider="REDSHIFT",
            staging_prefix="test",
            stage=AsyncMock(),
            apply=AsyncMock(),
            marker_exists=AsyncMock(return_value=False),
            classify=classify,
        )
    assert batch.status == "APPLIED"
    assert batch.last_error_code == "REDSHIFT_DESTINATION_MARKER_MISSING"
    assert consumer.commits == []


async def test_expired_pending_batch_never_uses_earliest_fallback(monkeypatch):
    current = delivery()
    plan = plan_batch(str(current.id), [normalize_event(event_record())], "test")
    batch = WarehouseBatchLedger(
        delivery_id=current.id,
        batch_id=plan.batch_id,
        source_offsets_json=plan.source_offsets,
        staging_manifest_json={"record_count": 1},
        status="STAGING",
    )
    consumer = FakeConsumer()
    consumer.beginning_offsets = AsyncMock(
        side_effect=lambda partitions: dict.fromkeys(partitions, 9)
    )
    monkeypatch.setattr("app.services.warehouses.staged.AIOKafkaConsumer", lambda *a, **k: consumer)
    from app.core.errors import DomainError

    def permanent(error):
        return {"code": error.code, "retryable": False}

    with pytest.raises(DomainError) as error:
        await process_staged_once(
            fake_session(batch),
            current,
            KafkaCluster(
                name="Kafka", bootstrap_servers="kafka:29092", security_protocol="PLAINTEXT"
            ),
            {},
            provider="AZURE_SYNAPSE",
            staging_prefix="test",
            stage=AsyncMock(),
            apply=AsyncMock(),
            marker_exists=AsyncMock(),
            classify=permanent,
        )
    assert error.value.code == "WAREHOUSE_RECOVERY_HISTORY_EXPIRED"
    assert batch.status == "TERMINAL_FAILED"
    assert consumer.commits == []


@pytest.mark.parametrize("provider", ["REDSHIFT", "AZURE_SYNAPSE"])
async def test_apply_retry_reuses_durable_upload_without_advancing_checkpoint(
    monkeypatch, provider
):
    current = delivery()
    consumer = FakeConsumer({"partition": [event_record()]})
    monkeypatch.setattr("app.services.warehouses.staged.AIOKafkaConsumer", lambda *a, **k: consumer)
    session = fake_session(None, None)
    stage = AsyncMock(return_value={"data_uri": "s3://fixture/original.csv"})
    apply = AsyncMock(side_effect=TimeoutError("destination unavailable"))
    args = dict(
        provider=provider,
        staging_prefix="test",
        stage=stage,
        apply=apply,
        marker_exists=AsyncMock(return_value=True),
        classify=classify,
    )
    kafka = KafkaCluster(
        name="Kafka", bootstrap_servers="kafka:29092", security_protocol="PLAINTEXT"
    )
    with pytest.raises(TimeoutError):
        await process_staged_once(session, current, kafka, {}, **args)
    batch = session.add.call_args.args[0]
    assert batch.status == "RETRYABLE_FAILED" and not consumer.commits
    apply.side_effect = None
    await process_staged_once(fake_session(batch), current, kafka, {}, **args)
    assert batch.status == "CHECKPOINTED"
    assert stage.await_count == 1 and apply.await_count == 2
    assert next(iter(consumer.commits[0].values())).offset == 8


async def test_synapse_entrypoint_preserves_existing_group_and_shared_authority(monkeypatch):
    from app.models.entities import Connection
    from app.services.azure_synapse.runtime import process_once

    shared = AsyncMock(return_value={"status": "IDLE"})
    monkeypatch.setattr("app.services.warehouses.staged.process_staged_once", shared)
    current = delivery()
    await process_once(
        fake_session(),
        current,
        Connection(name="test", config_json={"staging_prefix": "test-only"}),
        {},
        KafkaCluster(name="Kafka", bootstrap_servers="kafka:29092", security_protocol="PLAINTEXT"),
        {},
    )
    assert shared.await_args.kwargs["consumer_group_prefix"] == "cluecdc-synapse"
    assert shared.await_args.args[1] is current


@pytest.mark.parametrize("provider", ["REDSHIFT", "AZURE_SYNAPSE"])
async def test_legacy_unstaged_batch_without_count_requires_explicit_reconciliation(
    monkeypatch, provider
):
    from app.core.errors import DomainError

    current = delivery()
    batch = WarehouseBatchLedger(
        delivery_id=current.id,
        batch_id="legacy",
        source_offsets_json={"capture.public.orders:0": {"first": 7, "last": 9}},
        staging_manifest_json={},
        status="STAGING",
    )
    consumer = FakeConsumer()
    monkeypatch.setattr("app.services.warehouses.staged.AIOKafkaConsumer", lambda *a, **k: consumer)
    stage, apply = AsyncMock(), AsyncMock()
    with pytest.raises(DomainError) as error:
        await process_staged_once(
            fake_session(batch),
            current,
            KafkaCluster(
                name="Kafka", bootstrap_servers="kafka:29092", security_protocol="PLAINTEXT"
            ),
            {},
            provider=provider,
            staging_prefix="test",
            stage=stage,
            apply=apply,
            marker_exists=AsyncMock(),
            classify=classify,
        )
    assert error.value.code == "WAREHOUSE_RECOVERY_COUNT_UNAVAILABLE"
    assert batch.status == "TERMINAL_FAILED" and not consumer.commits
    stage.assert_not_awaited()
    apply.assert_not_awaited()


@pytest.mark.parametrize("boundary", ["upload", "destination_apply"])
async def test_crash_recovery_uses_persisted_ledger_in_a_new_session(
    db_factory, monkeypatch, boundary
):
    from aiokafka.structs import TopicPartition
    from sqlalchemy import select

    from tests.test_connector_recovery import recovery_entities

    class ProcessCrash(BaseException):
        pass

    delivery_id, _, _ = await recovery_entities(db_factory)
    tp = TopicPartition("capture.public.orders", 0)
    consumer = FakeConsumer({tp: [event_record(7)]})
    monkeypatch.setattr("app.services.warehouses.staged.AIOKafkaConsumer", lambda *a, **k: consumer)
    uploads, committed_batches = [], set()
    crash = True

    async def stage(key, body, manifest):
        uploads.append((key, body))
        if crash and boundary == "upload":
            raise ProcessCrash()
        return {**manifest, "data_uri": "s3://fixture/" + key}

    async def apply(batch_id, uri):
        # Model the provider's transaction+marker contract; this test does not
        # certify the real Redshift/Synapse transaction engine.
        committed_batches.add(batch_id)
        if crash and boundary == "destination_apply":
            raise ProcessCrash()

    async def marker(batch_id):
        return batch_id in committed_batches

    options = dict(
        provider="REDSHIFT",
        staging_prefix="test",
        stage=stage,
        apply=apply,
        marker_exists=marker,
        classify=classify,
    )
    async with db_factory() as db:
        current = await db.get(PipelineDestination, delivery_id)
        current.topic_mapping_json = delivery().topic_mapping_json
        current.configuration_json = {"batch_size": 100, "batch_flush_interval_ms": 1000}
        await db.commit()
        kafka = await db.scalar(select(KafkaCluster))
        with pytest.raises(ProcessCrash):
            await process_staged_once(db, current, kafka, {}, **options)
    assert not consumer.commits
    # A different Kafka poll after process death must not replace the saved batch.
    consumer.records = {tp: [event_record(7), event_record(8)]}
    crash = False
    async with db_factory() as db:
        persisted = await db.scalar(select(WarehouseBatchLedger))
        assert persisted.status == ("STAGING" if boundary == "upload" else "APPLYING")
        assert persisted.source_offsets_json == {"capture.public.orders:0": {"first": 7, "last": 7}}
        original_batch = persisted.batch_id
        await process_staged_once(
            db,
            await db.get(PipelineDestination, delivery_id),
            await db.scalar(select(KafkaCluster)),
            {},
            **options,
        )
        assert persisted.status == "CHECKPOINTED"
    assert committed_batches == {original_batch}
    assert next(iter(consumer.commits[0].values())).offset == 8
    assert all(body == uploads[0][1] for _, body in uploads)
    assert len(uploads) == (2 if boundary == "upload" else 1)
