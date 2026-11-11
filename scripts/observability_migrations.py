"""Seed nonempty 0015 metadata and verify additive 0016 upgrade/downgrade in an owned DB."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
from cdc_reliability_support import ROOT
from observability_fixture import attach


async def seed(url):
    sys.path.insert(0, str(ROOT / "apps/api"))
    from app.core import config

    settings = config.Settings(
        _env_file=None,
        database_url=url,
        environment="test",
        auth_mode="developer",
        connect_secret_token="observability-fixture-token-only-0001",
        secret_encryption_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    )
    config.get_settings = lambda: settings
    from sqlalchemy import insert
    from app.core.database import Session, engine
    from app.models.entities import (
        Alert,
        AlertRule,
        AuthSession,
        Connection,
        ConnectCluster,
        DataQualityCheck,
        DataQualityRule,
        KafkaCluster,
        Pipeline,
        PipelineDestination,
        PipelineMetric,
        PipelineTable,
        User,
        WarehouseBatchLedger,
        now,
    )
    from app.services.secrets import secret_provider

    async with Session() as db:
        reference = await secret_provider(db).put_secret(
            {"password": "owned-migration-sentinel"}
        )
        source = Connection(
            name="preserved",
            category="DATABASE",
            provider="POSTGRESQL",
            secret_ref=reference,
            capabilities_json=["SOURCE", "DESTINATION"],
            config_json={"host": "owned"},
        )
        kafka = KafkaCluster(name="preserved", bootstrap_servers="owned:9092")
        user = User(
            email="fixture@example.test",
            password_hash="preserved-hash",
            role="ADMIN",
            status="ACTIVE",
        )
        db.add_all([source, kafka, user])
        await db.flush()
        db.add(
            AuthSession(
                token_hash="a" * 64,
                user_id=user.id,
                expires_at=now() + timedelta(days=1),
            )
        )
        cluster = ConnectCluster(
            name="preserved", base_url="http://owned:8083", kafka_cluster_id=kafka.id
        )
        db.add(cluster)
        await db.flush()
        pipeline = Pipeline(
            name="preserved",
            source_id=source.id,
            kafka_cluster_id=kafka.id,
            connect_cluster_id=cluster.id,
            snapshot_mode="initial",
            topic_prefix="preserved",
            config_options={"slot.name": "preserve_slot", "source_position": "0/12345"},
        )
        db.add(pipeline)
        await db.flush()
        db.add_all(
            [
                AlertRule(name="preserved", event_types=["CONNECT_TASK_FAILED"]),
                Alert(
                    fingerprint="b" * 64,
                    event_type="CONNECT_TASK_FAILED",
                    severity="warning",
                    source_type="pipeline",
                    pipeline_id=pipeline.id,
                    component="connect",
                    title="preserved",
                    message="preserved",
                ),
                DataQualityCheck(
                    name="preserved",
                    source_connection_id=source.id,
                    destination_connection_id=source.id,
                    pipeline_id=pipeline.id,
                    config_json={"window": "all"},
                    created_by="fixture",
                    updated_by="fixture",
                ),
            ]
        )
        await db.flush()
        from sqlalchemy import select

        check = await db.scalar(select(DataQualityCheck))
        db.add(
            DataQualityRule(
                check_id=check.id, rule_type="ROW_COUNT", config_json={}, position=0
            )
        )
        delivery = PipelineDestination(
            pipeline_id=pipeline.id, destination_id=source.id, name="preserved"
        )
        db.add(delivery)
        await db.flush()
        db.add(
            WarehouseBatchLedger(
                delivery_id=delivery.id,
                batch_id="preserved-batch",
                status="CHECKPOINTED",
                source_offsets_json={"owned:0": {"start": 12, "end": 19}},
                applied_at=now(),
                checkpointed_at=now(),
            )
        )
        await db.execute(
            insert(PipelineMetric).values(
                pipeline_id=pipeline.id, values_json={"legacy_lag": 17}
            )
        )
        await db.execute(
            insert(PipelineTable).values(
                pipeline_id=pipeline.id,
                schema_name="public",
                table_name="preserved",
                topic_name="preserved.public.preserved",
                snapshot_status="COMPLETED",
            )
        )
        await db.commit()
        assert (await secret_provider(db).get_secret(reference))[
            "password"
        ] == "owned-migration-sentinel"
    await engine.dispose()


async def snapshot(dsn, columns=None):
    conn = await asyncpg.connect(dsn)
    try:
        if columns is None:
            rows = await conn.fetch(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name<>'alembic_version' ORDER BY table_name,ordinal_position"
            )
            columns = {}
            for row in rows:
                columns.setdefault(row["table_name"], []).append(row["column_name"])
        result = {}
        for table, names in columns.items():
            assert table.replace("_", "").isalnum() and all(
                n.replace("_", "").isalnum() for n in names
            )
            expr = ",".join('"' + n + '"' for n in names)
            rows = await conn.fetch(
                f'SELECT json_build_array({expr})::text AS row FROM "{table}" ORDER BY 1'
            )
            encoded = "\n".join(r["row"] for r in rows)
            result[table] = {
                "count": len(rows),
                "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            }
        return columns, result
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    stack = attach(args.fixture)
    name = "observability_migration_" + uuid4().hex
    port = int(stack.url("metadata-db", 5432).rsplit(":", 1)[1])
    password = stack.defaults["METADATA_PASSWORD"]

    async def provision():
        conn = await asyncpg.connect(
            host="127.0.0.1",
            port=port,
            user="cluecdc",
            password=password,
            database="cluecdc",
        )
        try:
            await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()

    asyncio.run(provision())
    dsn = f"postgresql://cluecdc:{password}@127.0.0.1:{port}/{name}"
    url = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "ENVIRONMENT": "test",
        "AUTH_MODE": "developer",
        "CONNECT_SECRET_TOKEN": "observability-fixture-token-only-0001",
        "SECRET_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    }
    logs = []

    def alembic(*command):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            cwd=ROOT / "apps/api",
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        logs.append(
            {
                "command": ["alembic", *command],
                "exit_code": result.returncode,
                "output": result.stdout + result.stderr,
            }
        )
        assert result.returncode == 0, "Alembic command failed"

    output = args.fixture.parent / "migrations.json"
    try:
        alembic("upgrade", "0015")
        asyncio.run(seed(url))
        columns, before = asyncio.run(snapshot(dsn))
        alembic("upgrade", "head")
        alembic("check")
        assert asyncio.run(snapshot(dsn, columns))[1] == before
        alembic("downgrade", "0015")
        assert asyncio.run(snapshot(dsn, columns))[1] == before
        alembic("upgrade", "head")
        alembic("check")
        assert asyncio.run(snapshot(dsn, columns))[1] == before
        output.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "database": name,
                    "preserved": before,
                    "commands": logs,
                },
                indent=2,
            )
        )
        print(
            "Populated 0015 -> 0016 -> 0015 -> 0016 preserved all seeded state; drift check passed"
        )
    except Exception:
        output.write_text(
            json.dumps({"status": "FAIL", "database": name, "commands": logs}, indent=2)
        )
        raise


if __name__ == "__main__":
    main()
