"""Prepare only the fixed disposable Data Quality browser fixture.

Start infrastructure/tests/data-quality.compose.yaml first. This script never
uses .env credentials, operator Connections, or the shared CDC Compose project.
"""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

ENVIRONMENT = {
    "DATABASE_URL": "postgresql+asyncpg://dq:dq-test-only@127.0.0.1:55435/dq_browser",
    "ENVIRONMENT": "test",
    "AUTH_MODE": "session",
    "SESSION_SECRET": "data-quality-test-session-secret-only-0001",
    "CONNECT_SECRET_TOKEN": "data-quality-test-service-token-only-0001",
    "SECRET_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    "BOOTSTRAP_DEFAULT_ADMIN": "false",
}
os.environ.update(ENVIRONMENT)

from argon2 import PasswordHasher  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.database import Session, engine  # noqa: E402
from app.models.entities import Connection, NotificationChannel, User  # noqa: E402
from app.services.secrets import secret_provider  # noqa: E402


async def database():
    conn = await asyncpg.connect(
        host="127.0.0.1", port=55435, user="dq", password="dq-test-only", database="dq"
    )
    try:
        if not await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname='dq_browser'"
        ):
            await conn.execute("CREATE DATABASE dq_browser")
    finally:
        await conn.close()


async def seed():
    timestamp = datetime.now(UTC) - timedelta(seconds=10)
    for port in (55436, 55437):
        conn = await asyncpg.connect(
            host="127.0.0.1",
            port=port,
            user="dq",
            password="dq-test-only",
            database="dq",
        )
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS public.dq_browser_items "
                "(id bigint PRIMARY KEY, updated_at timestamptz)"
            )
            await conn.execute(
                "INSERT INTO public.dq_browser_items VALUES(1,$1) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=EXCLUDED.updated_at",
                timestamp,
            )
            if not await conn.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname='dq_reader'"
            ):
                await conn.execute(
                    "CREATE ROLE dq_reader LOGIN PASSWORD 'dq-reader-test-only'"
                )
            await conn.execute("GRANT USAGE ON SCHEMA public TO dq_reader")
            await conn.execute("GRANT SELECT ON public.dq_browser_items TO dq_reader")
        finally:
            await conn.close()
    async with Session() as db:
        for role in ("ADMIN", "OPS", "VIEWER"):
            email = role.lower() + "@dq.test"
            user = await db.scalar(select(User).where(User.email == email))
            if not user:
                db.add(
                    User(
                        email=email,
                        role=role,
                        status="ACTIVE",
                        password_hash=PasswordHasher().hash("dq-browser-test-password"),
                    )
                )
        for side, port in (("source", 55436), ("destination", 55437)):
            name = "DQ Browser " + side.title()
            if not await db.scalar(select(Connection).where(Connection.name == name)):
                ref = await secret_provider(db).put_secret(
                    {"password": "dq-reader-test-only"}
                )
                db.add(
                    Connection(
                        name=name,
                        category="DATABASE",
                        provider="POSTGRESQL",
                        capabilities_json=[side.upper()],
                        secret_ref=ref,
                        config_json={
                            "host": "127.0.0.1",
                            "port": port,
                            "database_name": "dq",
                            "username": "dq_reader",
                        },
                    )
                )
        if not await db.scalar(
            select(NotificationChannel).where(
                NotificationChannel.name == "DQ Test Channel"
            )
        ):
            ref = await secret_provider(db).put_secret(
                {"url": "http://127.0.0.1:9/dq-test-only"}
            )
            db.add(
                NotificationChannel(
                    name="DQ Test Channel",
                    type="webhook",
                    enabled=True,
                    config_encrypted=ref,
                )
            )
        await db.commit()
    await engine.dispose()


def main():
    asyncio.run(database())
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "apps" / "api",
        env=os.environ,
        check=True,
    )
    asyncio.run(seed())
    print(
        "Prepared isolated dq_browser metadata, SELECT-only datasets, and three session users."
    )


if __name__ == "__main__":
    main()
