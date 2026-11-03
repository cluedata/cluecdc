"""Opt-in real databases from infrastructure/tests/data-quality.compose.yaml.

DQ_LIVE_TESTS=1 is explicit authorization to reset ONLY the disposable dq tables
on the fixed loopback test ports. No operator connection/environment is consulted.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import aiomysql
import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import (
    Base,
    Connection,
    DataQualityCheck,
    DataQualityRuleResult,
    DataQualityRun,
    Job,
    now,
)
from app.schemas.data_quality import CheckInput
from app.services import data_quality as service
from app.services import data_quality_engine as engine
from app.services.data_quality_measurements import Reader, reader
from app.services.secrets import secret_provider
from app.workers import worker

pytestmark = pytest.mark.skipif(
    os.getenv("DQ_LIVE_TESTS") != "1", reason="DQ_LIVE_TESTS=1 required"
)
PASSWORD = "dq-test-only"


async def setup_table(provider, port, count=2, age=1, kind="normal"):
    timestamp = datetime.now(UTC).replace(microsecond=123456) - timedelta(seconds=age)
    if provider == "POSTGRESQL":
        conn = await asyncpg.connect(
            host="127.0.0.1", port=port, user="dq", password=PASSWORD, database="dq"
        )
        try:
            await conn.execute("DROP TABLE IF EXISTS public.dq_items")
            extra = ", extra integer" if kind == "extra" else ""
            value_type = "text" if kind == "type" else "numeric(10,2)"
            updated = "" if kind == "missing" else ", updated_at timestamp with time zone"
            await conn.execute(
                "CREATE TABLE public.dq_items (id integer PRIMARY KEY, "
                f"amount {value_type}{updated}{extra})"
            )
            for index in range(count):
                if kind == "missing":
                    await conn.execute(
                        "INSERT INTO public.dq_items(id,amount) VALUES($1,$2)", index, 1
                    )
                else:
                    await conn.execute(
                        "INSERT INTO public.dq_items(id,amount,updated_at) VALUES($1,$2,$3)",
                        index,
                        "1" if kind == "type" else 1,
                        None if kind == "null" else timestamp,
                    )
            return await conn.fetchval("SELECT version()")
        finally:
            await conn.close()
    conn = await aiomysql.connect(
        host="127.0.0.1", port=port, user="dq", password=PASSWORD, db="dq", autocommit=True
    )
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP TABLE IF EXISTS dq_items")
            extra = ", extra integer" if kind == "extra" else ""
            value_type = "text" if kind == "type" else "decimal(10,2)"
            updated = "" if kind == "missing" else ", updated_at datetime(6)"
            await cursor.execute(
                "CREATE TABLE dq_items (id integer PRIMARY KEY, "
                f"amount {value_type}{updated}{extra})"
            )
            for index in range(count):
                if kind == "missing":
                    await cursor.execute(
                        "INSERT INTO dq_items(id,amount) VALUES(%s,%s)", (index, 1)
                    )
                else:
                    await cursor.execute(
                        "INSERT INTO dq_items(id,amount,updated_at) VALUES(%s,%s,%s)",
                        (index, 1, None if kind == "null" else timestamp.replace(tzinfo=None)),
                    )
            await cursor.execute("SELECT VERSION()")
            return (await cursor.fetchone())[0]
    finally:
        conn.close()


@pytest.fixture(params=["POSTGRESQL", "MYSQL"])
async def pair(request, db_factory):
    provider = request.param
    ports = [55436, 55437] if provider == "POSTGRESQL" else [33308, 33309]
    async with db_factory() as db:
        endpoints = []
        for side, port in zip(["source", "destination"], ports, strict=True):
            secret = await secret_provider(db).put_secret({"password": PASSWORD})
            endpoint = Connection(
                name=f"live-{side}",
                provider=provider,
                category="DATABASE",
                capabilities_json=[side.upper()],
                secret_ref=secret,
                config_json={
                    "host": "127.0.0.1",
                    "port": port,
                    "database_name": "dq",
                    "username": "dq",
                },
            )
            db.add(endpoint)
            await db.flush()
            endpoints.append(endpoint)
        await db.commit()
    schema = "public" if provider == "POSTGRESQL" else "dq"
    datasets = [
        {
            "connection_id": str(c.id),
            "database": "dq",
            "schema_name": schema,
            "table": "dq_items",
            "current_state_confirmed": True,
        }
        for c in endpoints
    ]
    return provider, ports, datasets, endpoints


SCENARIOS = [
    ("row_count", {}, 2, 1, "normal", "PASS"),
    ("row_count", {}, 1, 1, "normal", "FAIL"),
    ("row_count", {}, 0, 1, "empty_both", "PASS"),
    ("row_count_diff", {"tolerance": 1}, 1, 1, "normal", "PASS"),
    ("row_count_diff", {"tolerance": 0}, 1, 1, "normal", "FAIL"),
    ("row_count_diff", {"mode": "PERCENTAGE", "tolerance": 50}, 1, 1, "normal", "PASS"),
    ("row_count_diff", {"mode": "PERCENTAGE", "tolerance": 100}, 1, 1, "zero_source", "FAIL"),
    ("last_updated", {"tolerance_seconds": 3}, 2, 1, "normal", "PASS"),
    ("last_updated", {"tolerance_seconds": 1}, 2, 100, "normal", "FAIL"),
    ("last_updated", {}, 0, 1, "empty_both", "INCONCLUSIVE"),
    ("last_updated", {}, 2, 1, "null", "INCONCLUSIVE"),
    ("freshness_delay", {}, 2, 2, "normal", "PASS"),
    ("freshness_delay", {}, 2, 600, "normal", "FAIL"),
    ("freshness_delay", {}, 2, -10, "normal", "INCONCLUSIVE"),
    ("freshness_delay", {}, 2, 1000, "idle", "INCONCLUSIVE"),
    ("freshness_delay", {}, 2, 1, "null", "INCONCLUSIVE"),
    ("data_age", {}, 2, 1, "normal", "PASS"),
    ("data_age", {"activity_policy": "EXPECT_NEW_DATA"}, 2, 1000, "normal", "FAIL"),
    ("data_age", {}, 2, 1000, "idle", "INCONCLUSIVE"),
    ("data_age", {}, 2, 1, "source_null", "INCONCLUSIVE"),
    ("schema_match", {}, 2, 1, "normal", "PASS"),
    ("schema_match", {}, 2, 1, "missing", "FAIL"),
    ("schema_match", {}, 2, 1, "type", "FAIL"),
    ("schema_match", {}, 2, 1, "extra", "PASS"),
]


@pytest.mark.parametrize("kind,options,dest_count,dest_age,scenario,expected", SCENARIOS)
async def test_real_rule_scenarios(
    pair, db_factory, monkeypatch, kind, options, dest_count, dest_age, scenario, expected
):
    provider, ports, datasets, _ = pair
    source_version = await setup_table(
        provider,
        ports[0],
        count=0 if scenario in {"empty_both", "zero_source"} else 2,
        age=900 if scenario == "idle" else 1,
        kind="null" if scenario == "source_null" else "normal",
    )
    destination_version = await setup_table(
        provider,
        ports[1],
        count=dest_count,
        age=dest_age,
        kind=scenario if scenario in {"null", "missing", "type", "extra"} else "normal",
    )
    rule = {"type": kind, **options}
    if kind in {"last_updated", "freshness_delay", "data_age"}:
        rule.update(
            source_column="updated_at",
            destination_column="updated_at",
            semantics="BUSINESS_UPDATED",
            source_timezone="UTC",
            destination_timezone="UTC",
        )
    config = CheckInput.model_validate(
        {"name": "real " + kind, "source": datasets[0], "destination": datasets[1], "rules": [rule]}
    )
    # Source/destination setup observations are independent; allow a few seconds precision drift.
    if kind == "freshness_delay" and expected == "PASS":
        config.rules[0].max_delay_seconds = 10
    async with db_factory() as db:
        item = await service.save(db, config, "real-e2e")
        run = await service.enqueue(db, item, "real-e2e")
        run_id = run.id
        await db.commit()
    monkeypatch.setattr(worker, "Session", db_factory)
    await worker.run_job()
    async with db_factory() as db:
        run = await db.get(DataQualityRun, run_id)
        assert run.quality == expected, (provider, scenario, run.error_code)
        assert run.consistency == "BEST_EFFORT"
        evidence_dir = os.getenv("DQ_EVIDENCE_DIR")
        if evidence_dir:
            persisted = (
                await db.scalars(
                    select(DataQualityRuleResult).where(DataQualityRuleResult.run_id == run.id)
                )
            ).all()
            path = Path(evidence_dir)
            path.mkdir(parents=True, exist_ok=True)
            with (path / "live-results.jsonl").open("a", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        {
                            "provider": provider,
                            "source_version": source_version,
                            "destination_version": destination_version,
                            "rule": kind,
                            "scenario": scenario,
                            "options": options,
                            "destination_count": dest_count,
                            "destination_age": dest_age,
                            "expected": expected,
                            "actual": run.quality,
                            "run_id": str(run.id),
                            "execution_status": run.status,
                            "consistency": run.consistency,
                            "evidence": [r.evidence_json for r in persisted],
                            "status": "PASS",
                        }
                    )
                    + "\n"
                )


async def test_real_schema_widening_unknown_types_and_concurrent_observations(pair, db_factory):
    provider, ports, datasets, endpoints = pair
    for port in ports:
        await setup_table(provider, port)
    if provider == "POSTGRESQL":
        conn = await asyncpg.connect(
            host="127.0.0.1", port=ports[1], user="dq", password=PASSWORD, database="dq"
        )
        await conn.execute("ALTER TABLE public.dq_items ALTER COLUMN id TYPE bigint")
        await conn.close()
    else:
        conn = await aiomysql.connect(
            host="127.0.0.1", port=ports[1], user="dq", password=PASSWORD, db="dq", autocommit=True
        )
        async with conn.cursor() as cursor:
            await cursor.execute("ALTER TABLE dq_items MODIFY COLUMN id bigint NOT NULL")
        conn.close()
    config = CheckInput.model_validate(
        {
            "name": "widening",
            "source": datasets[0],
            "destination": datasets[1],
            "rules": [{"type": "schema_match", "id": str(uuid4())}],
        }
    )
    async with db_factory() as db:
        results, _ = await engine.execute(db, config, now())
        assert results[0]["quality"] == "PASS"
        for side, port in zip(["source", "destination"], ports, strict=True):
            if provider == "POSTGRESQL":
                conn = await asyncpg.connect(
                    host="127.0.0.1", port=port, user="dq", password=PASSWORD, database="dq"
                )
                await conn.execute("ALTER TABLE public.dq_items ADD COLUMN custom interval")
                await conn.close()
            else:
                conn = await aiomysql.connect(
                    host="127.0.0.1",
                    port=port,
                    user="dq",
                    password=PASSWORD,
                    db="dq",
                    autocommit=True,
                )
                async with conn.cursor() as cursor:
                    await cursor.execute("ALTER TABLE dq_items ADD COLUMN custom enum('a','b')")
                conn.close()
        results, _ = await engine.execute(db, config, now())
        assert results[0]["quality"] == "INCONCLUSIVE"
        async with reader(db, endpoints[0]) as source:
            cols = await source.columns(config.source)
            observed = await source.measure(config.source, cols, config.comparison, "source", {})
        # An independent commit after Source observation demonstrates count races explicitly.
        if provider == "POSTGRESQL":
            conn = await asyncpg.connect(
                host="127.0.0.1", port=ports[1], user="dq", password=PASSWORD, database="dq"
            )
            await conn.execute("INSERT INTO public.dq_items(id,amount) VALUES(99,1)")
            await conn.close()
        else:
            conn = await aiomysql.connect(
                host="127.0.0.1",
                port=ports[1],
                user="dq",
                password=PASSWORD,
                db="dq",
                autocommit=True,
            )
            async with conn.cursor() as cursor:
                await cursor.execute("INSERT INTO dq_items(id,amount) VALUES(99,1)")
            conn.close()
        async with reader(db, endpoints[1]) as destination:
            cols = await destination.columns(config.destination)
            dest_observed = await destination.measure(
                config.destination, cols, config.comparison, "destination", {}
            )
        from app.schemas.data_quality import RowCount

        result = engine.evaluate(RowCount(type="row_count"), observed, dest_observed, now())
        assert result["quality"] == "FAIL"
        assert "BEST_EFFORT" in engine.LIMITATIONS[0]


async def test_shared_measurements_filters_windows_discovery_readonly(
    pair, db_factory, monkeypatch
):
    provider, ports, datasets, endpoints = pair
    for port in ports:
        await setup_table(provider, port)
    schema = datasets[0]["schema_name"]
    config = CheckInput.model_validate(
        {
            "name": "shared",
            "source": datasets[0],
            "destination": datasets[1],
            "rules": [
                {"type": "row_count", "id": str(uuid4())},
                {"type": "row_count_diff", "id": str(uuid4())},
            ],
            "comparison": {
                "scope": "TIME_WINDOW",
                "source_time_column": "updated_at",
                "destination_time_column": "updated_at",
                "source_timezone": "UTC",
                "destination_timezone": "UTC",
                "settling_seconds": 0,
                "source_filters": [{"column": "id", "operator": "gte", "value": 0}],
                "destination_filters": [{"column": "id", "operator": "gte", "value": 0}],
            },
        }
    )
    counts = []
    original = Reader.rows

    async def tracked(self, sql, values=None):
        if "COUNT(*)" in sql:
            counts.append(sql)
        return await original(self, sql, values)

    monkeypatch.setattr(Reader, "rows", tracked)
    async with db_factory() as db:
        results, window = await engine.execute(db, config, now())
        assert all(r["quality"] == "PASS" for r in results)
        assert len(counts) == 2 and window
        async with reader(db, endpoints[0]) as r:
            assert {"schema_name": schema, "table_name": "dq_items"} in await r.tables("dq")
            with pytest.raises(Exception):
                await r.rows(f"UPDATE {r.table(config.source)} SET id=id")


async def test_provider_pair_and_timezone_guards(pair, db_factory):
    provider, ports, datasets, endpoints = pair
    for port in ports:
        await setup_table(provider, port)
    config = CheckInput.model_validate(
        {
            "name": "guards",
            "source": datasets[0],
            "destination": datasets[1],
            "rules": [
                {
                    "type": "last_updated",
                    "source_column": "missing",
                    "destination_column": "updated_at",
                    "semantics": "BUSINESS_UPDATED",
                }
            ],
        }
    )
    async with db_factory() as db:
        _, _, cap = await engine.inspect(db, config, now())
        assert cap["status"] == "REQUIRES_CONFIGURATION"
        endpoints[0].provider, endpoints[1].provider = "MYSQL", "POSTGRESQL"
        config.source.schema_name, config.destination.schema_name = "dq", "public"
        db.add_all(endpoints)
        await db.flush()
        from app.core.errors import DomainError

        with pytest.raises(DomainError) as error:
            await engine.connections(db, config)
        assert error.value.code == "DQ_PAIR_UNVERIFIED"


async def test_multiworker_due_claim_and_restart(monkeypatch):
    # Real PostgreSQL row locks rather than SQLite's in-process serialization.
    import asyncio

    metadata = create_async_engine("postgresql+asyncpg://dq:dq-test-only@127.0.0.1:55435/dq")
    factory = async_sessionmaker(metadata, expire_on_commit=False)
    async with metadata.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            source = Connection(name=f"scheduler-{uuid4()}", type="postgresql", database_name="dq")
            db.add(source)
            await db.flush()
            ds = {
                "connection_id": str(source.id),
                "database": "dq",
                "schema_name": "public",
                "table": "test",
                "current_state_confirmed": True,
            }
            cfg = CheckInput.model_validate(
                {
                    "name": "scheduler",
                    "source": ds,
                    "destination": ds,
                    "rules": [{"type": "row_count", "id": str(uuid4())}],
                    "schedule": {"enabled": True},
                }
            )
            check = DataQualityCheck(
                name=cfg.name,
                source_connection_id=source.id,
                destination_connection_id=source.id,
                config_json=cfg.model_dump(mode="json"),
                next_run_at=now() - timedelta(seconds=1),
                created_by="e2e",
                updated_by="e2e",
            )
            db.add(check)
            await db.commit()
            identifier = check.id

        async def tick():
            async with factory() as db:
                count = await service.schedule_due(db)
                await db.commit()
                return count

        assert sum(await asyncio.gather(tick(), tick(), tick())) == 1
        monkeypatch.setattr(worker, "Session", factory)
        claimed = await worker._claim_job("interrupted-worker")
        assert claimed and claimed[1] == "data_quality"
        async with factory() as db:
            job = await db.get(Job, claimed[0])
            job.lease_expires_at = now() - timedelta(seconds=1)
            await db.commit()
        await worker._claim_job("replacement-worker")
        async with factory() as db:
            job = await db.get(Job, claimed[0])
            assert job.status == "PENDING"
            job.next_attempt_at = now() - timedelta(seconds=1)
            await db.commit()
        assert await worker._claim_job("replacement-worker") == claimed
        async with factory() as db:
            check = await db.get(DataQualityCheck, identifier)
            assert check.active_run_id is not None
            # Cleanup this isolated test's metadata without touching any other test run.
            job = await db.get(Job, claimed[0])
            job.status = "FAILED"
            await db.commit()
            await service.schedule_due(db)
            check.enabled, check.next_run_at = False, None
            await db.commit()
            run = await db.scalar(
                select(DataQualityRun).where(DataQualityRun.check_id == identifier)
            )
            run.finished_at = now() - timedelta(days=91)
            expired_id = run.id
            await db.commit()
            await service.cleanup(db)
            await db.commit()
            assert await db.get(DataQualityRun, expired_id) is None
    finally:
        await metadata.dispose()


async def test_real_scheduled_execution_and_manual_schedule_independence(
    pair, db_factory, monkeypatch
):
    provider, ports, datasets, _ = pair
    for port in ports:
        await setup_table(provider, port)
    config = CheckInput.model_validate(
        {
            "name": "scheduled-real",
            "source": datasets[0],
            "destination": datasets[1],
            "rules": [{"type": "row_count"}],
            "schedule": {"enabled": True},
        }
    )
    async with db_factory() as db:
        item = await service.save(db, config, "scheduled-real")
        await db.commit()
        due = now() + timedelta(seconds=601)
        assert await service.schedule_due(db, due) == 1
        await db.commit()
        run = await db.scalar(select(DataQualityRun))
        assert (
            run.trigger == "SCHEDULED" and run.snapshot_json["schedule"]["frequency_seconds"] == 600
        )
        run_id, check_id = run.id, item.id
        next_run = item.next_run_at
    monkeypatch.setattr(worker, "Session", db_factory)
    await worker.run_job()
    async with db_factory() as db:
        run = await db.get(DataQualityRun, run_id)
        assert run.status == "COMPLETED" and run.quality == "PASS"
        item = await db.get(DataQualityCheck, check_id)
        await service.enqueue(db, item, "manual-after-scheduled")
        assert engine.aware(item.next_run_at) == engine.aware(next_run)
        await db.commit()
    await worker.run_job()
    async with db_factory() as db:
        runs = (await db.scalars(select(DataQualityRun))).all()
        assert len(runs) == 2 and all(r.quality == "PASS" for r in runs)


async def test_temporal_column_named_count_does_not_collide_with_count_measurement(
    pair, db_factory
):
    provider, ports, datasets, _ = pair
    for port in ports:
        await setup_table(provider, port)
        if provider == "POSTGRESQL":
            conn = await asyncpg.connect(
                host="127.0.0.1", port=port, user="dq", password=PASSWORD, database="dq"
            )
            await conn.execute('ALTER TABLE public.dq_items RENAME COLUMN updated_at TO "count"')
            await conn.close()
        else:
            conn = await aiomysql.connect(
                host="127.0.0.1", port=port, user="dq", password=PASSWORD, db="dq", autocommit=True
            )
            async with conn.cursor() as cursor:
                await cursor.execute("ALTER TABLE dq_items RENAME COLUMN updated_at TO `count`")
            conn.close()
    config = CheckInput.model_validate(
        {
            "name": "measurement-identity",
            "source": datasets[0],
            "destination": datasets[1],
            "rules": [
                {"type": "row_count", "id": str(uuid4())},
                {
                    "type": "last_updated",
                    "id": str(uuid4()),
                    "source_column": "count",
                    "destination_column": "count",
                    "semantics": "BUSINESS_UPDATED",
                    "source_timezone": "UTC",
                    "destination_timezone": "UTC",
                    "tolerance_seconds": 5,
                },
            ],
        }
    )
    async with db_factory() as db:
        results, _ = await engine.execute(db, config, now())
        assert all(r["quality"] == "PASS" for r in results)
        assert results[0]["source"]["value"] == 2


async def test_real_timestamp_precision_and_fixed_half_open_windows(pair, db_factory):
    provider, ports, datasets, _ = pair
    stamp = datetime(2026, 11, 3, 0, 0, 0, 123456, tzinfo=UTC)

    async def set_stamps(port, start, end, reduce_precision=False):
        if provider == "POSTGRESQL":
            conn = await asyncpg.connect(
                host="127.0.0.1", port=port, user="dq", password=PASSWORD, database="dq"
            )
            try:
                if reduce_precision:
                    await conn.execute(
                        "ALTER TABLE public.dq_items ALTER COLUMN updated_at "
                        "TYPE timestamp(3) with time zone"
                    )
                await conn.execute(
                    "UPDATE public.dq_items SET updated_at=CASE WHEN id=0 "
                    "THEN $1::timestamptz ELSE $2::timestamptz END",
                    start,
                    end,
                )
            finally:
                await conn.close()
        else:
            conn = await aiomysql.connect(
                host="127.0.0.1", port=port, user="dq", password=PASSWORD, db="dq", autocommit=True
            )
            try:
                async with conn.cursor() as cursor:
                    if reduce_precision:
                        await cursor.execute(
                            "ALTER TABLE dq_items MODIFY COLUMN updated_at datetime(3)"
                        )
                    await cursor.execute(
                        "UPDATE dq_items SET updated_at=CASE WHEN id=0 THEN %s ELSE %s END",
                        (start.replace(tzinfo=None), end.replace(tzinfo=None)),
                    )
            finally:
                conn.close()

    for index, port in enumerate(ports):
        await setup_table(provider, port)
        await set_stamps(port, stamp, stamp, index == 1)
    cfg = CheckInput.model_validate(
        {
            "name": "temporal-precision",
            "source": datasets[0],
            "destination": datasets[1],
            "rules": [
                {
                    "type": "last_updated",
                    "id": str(uuid4()),
                    "source_column": "updated_at",
                    "destination_column": "updated_at",
                    "source_timezone": "UTC",
                    "destination_timezone": "UTC",
                    "semantics": "BUSINESS_UPDATED",
                    "tolerance_seconds": 0.001,
                }
            ],
        }
    )
    async with db_factory() as db:
        results, _ = await engine.execute(db, cfg, now())
        assert results[0]["quality"] == "PASS"
        assert 0 < results[0]["difference_seconds"] <= 0.001
        cfg.rules[0].tolerance_seconds = 0
        assert (await engine.execute(db, cfg, now()))[0][0]["quality"] == "FAIL"
    start, end = stamp.replace(microsecond=0), stamp.replace(microsecond=0) + timedelta(minutes=1)
    for port in ports:
        await set_stamps(port, start, end)
    cfg = CheckInput.model_validate(
        {
            "name": "fixed-boundaries",
            "source": datasets[0],
            "destination": datasets[1],
            "rules": [{"type": "row_count", "id": str(uuid4())}],
            "comparison": {
                "scope": "TIME_WINDOW",
                "period": "FIXED",
                "fixed_start": start,
                "fixed_end": end,
                "source_time_column": "updated_at",
                "destination_time_column": "updated_at",
                "source_timezone": "UTC",
                "destination_timezone": "UTC",
            },
        }
    )
    async with db_factory() as db:
        results, window = await engine.execute(db, cfg, now())
        assert results[0]["quality"] == "PASS" and results[0]["source"]["value"] == 1
        assert results[0]["destination"]["value"] == 1
        assert window == {"start": start.isoformat(), "end": end.isoformat()}
