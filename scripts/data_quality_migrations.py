"""Exercise upgrade/downgrade only in the fixed disposable dq_migration database."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
ENV = {
    **os.environ,
    "DATABASE_URL": "postgresql+asyncpg://dq:dq-test-only@127.0.0.1:55435/dq_migration",
    "ENVIRONMENT": "test",
    "AUTH_MODE": "session",
    "BOOTSTRAP_DEFAULT_ADMIN": "false",
    "CONNECT_SECRET_TOKEN": "dq-migration-test-only-service-token-00001",
    "SECRET_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
}


async def provision():
    conn = await asyncpg.connect(
        host="127.0.0.1", port=55435, user="dq", password="dq-test-only", database="dq"
    )
    try:
        if not await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname='dq_migration'"
        ):
            await conn.execute("CREATE DATABASE dq_migration")
    finally:
        await conn.close()


async def snapshot():
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=55435,
        user="dq",
        password="dq-test-only",
        database="dq_migration",
    )
    try:
        return {
            name: dict(
                await conn.fetchrow(
                    f"SELECT COUNT(*) AS count, md5(coalesce(json_agg(t ORDER BY id)::text,'[]')) AS digest FROM \"{name}\" t"
                )
            )
            for name in (
                "connections",
                "pipelines",
                "pipeline_destinations",
                "users",
                "secret_references",
                "warehouse_batch_ledger",
            )
        }
    finally:
        await conn.close()


async def seed_sentinels():
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=55435,
        user="dq",
        password="dq-test-only",
        database="dq_migration",
    )
    try:
        secret_id = uuid5(NAMESPACE_URL, "cluecdc-dq-migration-secret")
        connection_id = uuid5(NAMESPACE_URL, "cluecdc-dq-migration-connection")
        await conn.execute(
            "INSERT INTO secret_references(id,created_at,updated_at,ciphertext,provider) "
            "VALUES($1,now(),now(),'migration-sentinel-no-real-secret','encrypted-database') ON CONFLICT(id) DO NOTHING",
            secret_id,
        )
        await conn.execute(
            "INSERT INTO connections(id,created_at,updated_at,name,category,provider,status,description,config_json,capabilities_json,secret_ref) "
            "VALUES($1,now(),now(),'dq-migration-sentinel','DATABASE','POSTGRESQL','UNKNOWN','preserve identity','{}','[\"SOURCE\"]',$2) ON CONFLICT(id) DO NOTHING",
            connection_id,
            secret_id,
        )
    finally:
        await conn.close()


def run(*arguments):
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT / "apps" / "api",
        env=ENV,
        check=True,
    )


def main():
    asyncio.run(provision())
    run("upgrade", "head")
    asyncio.run(seed_sentinels())
    before = asyncio.run(snapshot())
    run("check")
    run("downgrade", "0014")
    assert asyncio.run(snapshot()) == before
    run("upgrade", "head")
    run("check")
    assert asyncio.run(snapshot()) == before
    output = ROOT / "artifacts" / "data-quality" / "migrations.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "status": "PASS",
                "revision": "0015",
                "upgrade": True,
                "downgrade_to_0014": True,
                "reupgrade": True,
                "alembic_drift": False,
                "unrelated_counts_preserved": before,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Data Quality migration round trip passed; unrelated table counts preserved.")


if __name__ == "__main__":
    main()
