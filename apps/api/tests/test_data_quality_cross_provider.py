"""Real, isolated PostgreSQL -> MySQL value and schema-contract verification."""

import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import aiomysql
import asyncpg
import pytest
from sqlalchemy import select

from app.models.entities import Connection, DataQualityRuleResult, DataQualityRun
from app.schemas.data_quality import CheckInput
from app.services import data_quality as service
from app.services.data_quality_measurements import Reader, reader
from app.services.secrets import secret_provider
from app.workers import worker
from tests.test_data_quality_live import PASSWORD, SCENARIOS, setup_table

pytestmark = pytest.mark.skipif(
    os.getenv("DQ_LIVE_TESTS") != "1", reason="DQ_LIVE_TESTS=1 required"
)


@pytest.fixture
async def cross_pair(db_factory):
    datasets, endpoints = [], []
    async with db_factory() as db:
        for side, provider, port, schema in (
            ("source", "POSTGRESQL", 55436, "public"),
            ("destination", "MYSQL", 33309, "dq"),
        ):
            reference = await secret_provider(db).put_secret({"password": PASSWORD})
            endpoint = Connection(
                name=f"cross-{side}",
                category="DATABASE",
                provider=provider,
                capabilities_json=[side.upper()],
                secret_ref=reference,
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
            datasets.append(
                {
                    "connection_id": str(endpoint.id),
                    "database": "dq",
                    "schema_name": schema,
                    "table": "dq_items",
                    "current_state_confirmed": True,
                }
            )
        await db.commit()
    return datasets, endpoints


def configuration(datasets, kind, options=None):
    rule = {"type": kind, **(options or {})}
    if kind in {"last_updated", "freshness_delay", "data_age"}:
        rule.update(
            source_column="updated_at",
            destination_column="updated_at",
            semantics="BUSINESS_UPDATED",
            source_timezone="UTC",
            destination_timezone="UTC",
        )
    return CheckInput.model_validate(
        {
            "name": f"cross {kind}",
            "source": datasets[0],
            "destination": datasets[1],
            "rules": [rule],
        }
    )


async def execute_persisted(db_factory, monkeypatch, config):
    async with db_factory() as db:
        item = await service.save(db, config, "cross-provider-e2e")
        run = await service.enqueue(db, item, "cross-provider-e2e")
        run_id = run.id
        await db.commit()
    monkeypatch.setattr(worker, "Session", db_factory)
    assert await worker.run_job()
    async with db_factory() as db:
        run = await db.get(DataQualityRun, run_id)
        results = (
            await db.scalars(
                select(DataQualityRuleResult).where(DataQualityRuleResult.run_id == run_id)
            )
        ).all()
        return run, results


VALUE_SCENARIOS = [case for case in SCENARIOS if case[0] != "schema_match"]


@pytest.mark.parametrize("kind,options,dest_count,dest_age,scenario,expected", VALUE_SCENARIOS)
async def test_cross_provider_values(
    cross_pair, db_factory, monkeypatch, kind, options, dest_count, dest_age, scenario, expected
):
    datasets, _ = cross_pair
    source_version = await setup_table(
        "POSTGRESQL",
        55436,
        count=0 if scenario in {"empty_both", "zero_source"} else 2,
        age=900 if scenario == "idle" else 1,
        kind="null" if scenario == "source_null" else "normal",
    )
    destination_version = await setup_table(
        "MYSQL",
        33309,
        count=dest_count,
        age=dest_age,
        kind="null" if scenario == "null" else "normal",
    )
    run, results = await execute_persisted(
        db_factory, monkeypatch, configuration(datasets, kind, options)
    )
    assert run.status == "COMPLETED" and run.quality == expected
    assert results[0].evidence_json["provider_pair"] == ["POSTGRESQL", "MYSQL"]
    assert results[0].evidence_json["consistency"] == "BEST_EFFORT"
    directory = Path(os.getenv("DQ_EVIDENCE_DIR", "artifacts/data-quality"))
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "cross-provider-results.jsonl").open("a", encoding="utf-8") as output:
        output.write(
            json.dumps(
                {
                    "rule": kind,
                    "scenario": scenario,
                    "options": options,
                    "expected": expected,
                    "actual": run.quality,
                    "source_version": source_version,
                    "destination_version": destination_version,
                    "evidence": results[0].evidence_json,
                }
            )
            + "\n"
        )


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("integer", "PASS"),
        ("missing", "FAIL"),
        ("narrowing", "FAIL"),
        ("unverified_text", "INCONCLUSIVE"),
    ],
)
async def test_cross_provider_schema(cross_pair, db_factory, monkeypatch, scenario, expected):
    datasets, _ = cross_pair
    await setup_table("POSTGRESQL", 55436)
    await setup_table("MYSQL", 33309)
    pg = await asyncpg.connect(
        host="127.0.0.1", port=55436, user="dq", password=PASSWORD, database="dq"
    )
    mysql = await aiomysql.connect(
        host="127.0.0.1", port=33309, user="dq", password=PASSWORD, db="dq", autocommit=True
    )
    try:
        await pg.execute("ALTER TABLE public.dq_items DROP COLUMN amount, DROP COLUMN updated_at")
        async with mysql.cursor() as cursor:
            await cursor.execute("ALTER TABLE dq_items DROP COLUMN amount, DROP COLUMN updated_at")
            if scenario == "missing":
                await cursor.execute("ALTER TABLE dq_items RENAME COLUMN id TO other_id")
            elif scenario == "narrowing":
                await pg.execute("ALTER TABLE public.dq_items ALTER COLUMN id TYPE bigint")
            elif scenario == "unverified_text":
                await pg.execute("ALTER TABLE public.dq_items ADD COLUMN payload text")
                await cursor.execute("ALTER TABLE dq_items ADD COLUMN payload longtext")
    finally:
        await pg.close()
        mysql.close()
    run, results = await execute_persisted(
        db_factory, monkeypatch, configuration(datasets, "schema_match")
    )
    assert run.status == "COMPLETED" and run.quality == expected
    assert results[0].evidence_json["period_applied"] is False


async def test_cross_provider_utc_window_reuse_readonly_and_api(
    cross_pair, client, db_factory, monkeypatch
):
    datasets, endpoints = cross_pair
    await setup_table("POSTGRESQL", 55436)
    await setup_table("MYSQL", 33309)
    stamp = datetime.now(UTC).replace(microsecond=123000) - timedelta(seconds=10)
    pg = await asyncpg.connect(
        host="127.0.0.1", port=55436, user="dq", password=PASSWORD, database="dq"
    )
    try:
        await pg.execute(
            "UPDATE public.dq_items SET updated_at=$1",
            stamp.astimezone(timezone(timedelta(hours=7))),
        )
    finally:
        await pg.close()
    mysql = await aiomysql.connect(
        host="127.0.0.1", port=33309, user="dq", password=PASSWORD, db="dq", autocommit=True
    )
    try:
        async with mysql.cursor() as cursor:
            await cursor.execute("SET time_zone='+00:00'")
            await cursor.execute("ALTER TABLE dq_items MODIFY COLUMN updated_at timestamp(6) NULL")
            await cursor.execute("UPDATE dq_items SET updated_at=%s", (stamp.replace(tzinfo=None),))
    finally:
        mysql.close()
    config = configuration(datasets, "last_updated")
    config.rules.extend(
        [
            configuration(datasets, "row_count").rules[0],
            configuration(datasets, "row_count_diff").rules[0],
        ]
    )
    payload = config.model_dump(mode="json")
    payload["comparison"] = {
        "scope": "TIME_WINDOW",
        "period": "FIXED",
        "fixed_start": (stamp - timedelta(minutes=1)).isoformat(),
        "fixed_end": (stamp + timedelta(minutes=1)).isoformat(),
        "source_time_column": "updated_at",
        "destination_time_column": "updated_at",
        "source_timezone": "UTC",
        "destination_timezone": "UTC",
    }
    calls = []
    original = Reader.rows

    async def tracked(self, sql, values=None):
        if "COUNT(*)" in sql or "MAX(" in sql:
            calls.append(sql)
        return await original(self, sql, values)

    monkeypatch.setattr(Reader, "rows", tracked)
    response = await client.post("/api/v1/data-quality/checks", json=payload)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    queued = await client.post(f"/api/v1/data-quality/checks/{identifier}/run")
    assert queued.status_code == 202
    monkeypatch.setattr(worker, "Session", db_factory)
    await worker.run_job()
    result = await client.get(f"/api/v1/data-quality/runs/{queued.json()['id']}")
    assert result.json()["quality"] == "PASS"
    assert len(calls) == 4
    assert (await client.get(f"/api/v1/data-quality/checks/{identifier}/runs")).json()["items"]
    config = CheckInput.model_validate(response.json()["configuration"])
    async with db_factory() as db:
        for conn, dataset in zip(endpoints, [config.source, config.destination], strict=True):
            async with reader(db, conn) as database:
                with pytest.raises(Exception):
                    await database.rows(f"UPDATE {database.table(dataset)} SET id=id")
    payload["rules"][0]["destination_timezone"] = "Asia/Bangkok"
    rejected = await client.post("/api/v1/data-quality/checks", json=payload)
    assert rejected.status_code == 422
