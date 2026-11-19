"""Data Quality fixtures on an explicitly owned, disposable demo Compose project."""

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from scripts.cdc_acceptance_support import load_fixture_environment

ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def environment():
    return load_fixture_environment([ROOT / ".env"])


def compose(*arguments):
    project = os.environ.get("COMPOSE_PROJECT_NAME", "")
    if not project.startswith("cluecdc-test-"):
        raise RuntimeError(
            "Data Quality requires a disposable cluecdc-test-* Compose project"
        )
    return subprocess.check_output(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(ROOT / "docker-compose.yml"),
            "-f",
            str(ROOT / "docker-compose.demo.yml"),
            *arguments,
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        timeout=30,
    ).strip()


@lru_cache
def port(service, target):
    container = compose("ps", "-q", service)
    if not container:
        raise RuntimeError(
            "Start the disposable demo project before Data Quality fixtures"
        )
    info = json.loads(
        subprocess.check_output(
            ["docker", "inspect", container],
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    )[0]
    if (
        info["Config"]["Labels"]["com.docker.compose.project"]
        != os.environ["COMPOSE_PROJECT_NAME"]
    ):
        raise RuntimeError("Refusing an unowned Data Quality database")
    binding = info["NetworkSettings"]["Ports"][str(target) + "/tcp"][0]
    if binding["HostIp"] != "127.0.0.1":
        raise RuntimeError("Data Quality requires loopback fixture ports")
    return int(binding["HostPort"])


def postgres(side):
    metadata = side == "metadata"
    env = environment()
    return {
        "host": "127.0.0.1",
        "port": port("metadata-db" if metadata else "postgres-demo", 5432),
        "user": "cluecdc" if metadata else "postgres",
        "password": (env.get("METADATA_PASSWORD") or env["CLUECDC_LOCAL_PASSWORD"])
        if metadata
        else env["POSTGRES_DEMO_ADMIN_PASSWORD"],
        "database": "cluecdc" if metadata else "dq_" + side,
    }


def mysql(side):
    return {
        "host": "127.0.0.1",
        "port": port("mysql-demo", 3306),
        "user": "root",
        "password": environment()["MYSQL_DEMO_ADMIN_PASSWORD"],
        "db": "dq_" + side,
        "autocommit": True,
    }


def metadata_url(database):
    args = postgres("metadata")
    return (
        "postgresql+asyncpg://cluecdc:"
        + quote(args["password"], safe="")
        + "@127.0.0.1:"
        + str(args["port"])
        + "/"
        + database
    )


async def provision():
    import aiomysql
    import asyncpg

    conn = await asyncpg.connect(**postgres("metadata"))
    try:
        for name in ("dq_browser", "dq_migration", "dq_worker"):
            if not await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname=$1", name
            ):
                await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    args = postgres("source")
    args["database"] = "postgres"
    conn = await asyncpg.connect(**args)
    try:
        for name in ("dq_source", "dq_destination"):
            if not await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname=$1", name
            ):
                await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    args = mysql("source")
    args.pop("db")
    conn = await aiomysql.connect(**args)
    try:
        async with conn.cursor() as cursor:
            for name in ("dq_source", "dq_destination"):
                await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{name}`")
    finally:
        conn.close()
