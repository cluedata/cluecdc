"""Opt-in live source -> Kafka Connect -> MinIO acceptance.

Uses unique test tables, connectors, topics and buckets. Never touches existing
connector configurations or deletes topics/offsets/history. Fixture setup is
explicit here, never part of connection diagnostics. See docs/sources/README.md.
"""

import argparse
import asyncio
import gzip
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import boto3
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.core.errors import DomainError
from app.models.entities import Connection, Source
from app.providers import get_provider
from app.schemas.requests import (
    DeliveryInput,
    PipelineInput,
    TableSelection,
)
from app.services.destinations.providers import (
    ObjectStorageDeliveryProvider,
)


def wait_for(fn, label, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(1)
    raise RuntimeError("Timed out: " + label)


async def maria_setup(table):
    import aiomysql

    connection = await aiomysql.connect(
        host="127.0.0.1",
        port=13306,
        user="root",
        password="local-cdc-fixture-password",
        db="inventory",
        autocommit=True,
        charset="utf8mb4",
    )
    async with connection.cursor() as cursor:
        await cursor.execute(
            "GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, "
            "BINLOG MONITOR, LOCK TABLES ON *.* TO 'cdc'@'%'"
        )
        await cursor.execute(
            f"CREATE TABLE `{table}` (id INT PRIMARY KEY, "
            "label VARCHAR(255), amount DECIMAL(30,9))"
        )
        await cursor.execute(
            f"INSERT INTO `{table}` VALUES (1,'Ti\u1ebfng Vi\u1ec7t',12345678901234567890.123456789)"
        )
    connection.close()


async def maria_change(table, sql):
    import aiomysql

    connection = await aiomysql.connect(
        host="127.0.0.1",
        port=13306,
        user="root",
        password="local-cdc-fixture-password",
        db="inventory",
        autocommit=True,
        charset="utf8mb4",
    )
    async with connection.cursor() as cursor:
        await cursor.execute(sql.format(table=table))
    connection.close()


async def mongo_setup(table):
    from datetime import datetime, timezone

    from bson import Binary, Decimal128, ObjectId
    from pymongo import AsyncMongoClient

    client = AsyncMongoClient("mongodb://127.0.0.1:17017/?directConnection=true")
    hello = await client.admin.command("hello")
    if not hello.get("setName"):
        await client.admin.command(
            "replSetInitiate",
            {
                "_id": "cluecdc-rs",
                "members": [
                    {"_id": 0, "host": "cluecdc-source-fixtures-mongodb-source-1:27017"}
                ],
            },
        )
    for attempt in range(60):
        if (await client.admin.command("hello")).get("isWritablePrimary"):
            break
        await asyncio.sleep(1)
    await client.inventory[table].insert_one(
        {
            "_id": ObjectId("000000000000000000000001"),
            "label": "Ti\u1ebfng Vi\u1ec7t",
            "amount": Decimal128("12345678901234567890.123456789"),
            "nested": {"array": [1, "a", None, True]},
            "binary": Binary(b"\x00\xff"),
            "date": datetime(2026, 10, 30, tzinfo=timezone.utc),
        }
    )
    await client.close()


async def mongo_change(table, operation):
    from bson import ObjectId
    from pymongo import AsyncMongoClient

    client = AsyncMongoClient("mongodb://127.0.0.1:17017/?directConnection=true")
    collection = client.inventory[table]
    identity = ObjectId("000000000000000000000002")
    if operation == "insert":
        await collection.insert_one({"_id": identity, "label": "insert"})
    elif operation == "update":
        await collection.update_one({"_id": identity}, {"$set": {"label": "updated"}})
    elif operation == "delete":
        await collection.delete_one({"_id": identity})
    else:
        await collection.insert_one(
            {
                "_id": ObjectId(),
                "label": operation
                if operation.startswith("outage-")
                else "after-restart",
            }
        )
    await client.close()


def sqlserver_sql(sql, database="inventory"):
    import mssql_python

    c = mssql_python.connect(
        "Server=tcp:127.0.0.1,11433;Database=" + database + ";UID=sa;"
        "PWD={Local-CDC-Fixture-Password-123!};Encrypt=no;TrustServerCertificate=no;",
        autocommit=True,
        timeout=15,
    )
    try:
        cursor = c.cursor()
        cursor.execute(sql)
        cursor.close()
    finally:
        c.close()


async def sqlserver_setup(table):
    await asyncio.to_thread(
        sqlserver_sql,
        "IF DB_ID('inventory') IS NULL CREATE DATABASE inventory",
        "master",
    )
    await asyncio.to_thread(
        sqlserver_sql,
        (
            "IF (SELECT is_cdc_enabled FROM sys.databases WHERE "
            "name=DB_NAME())=0 EXEC sys.sp_cdc_enable_db"
        ),
    )
    await asyncio.to_thread(
        sqlserver_sql,
        f"CREATE TABLE dbo.[{table}] (id INT PRIMARY KEY,label NVARCHAR(255),amount DECIMAL(30,9));"
        f"INSERT INTO dbo.[{table}] VALUES(1,N'Ti\u1ebfng Vi\u1ec7t',12345678901234567890.123456789)",
    )
    await asyncio.to_thread(
        sqlserver_sql,
        f"EXEC sys.sp_cdc_enable_table @source_schema=N'dbo',@source_name=N'{table}',@role_name=NULL",
    )


async def change(kind, table, operation):
    if kind == "mongodb":
        return await mongo_change(table, operation)
    qualified = f"dbo.[{table}]" if kind == "sqlserver" else f"`{table}`"
    sql = {
        "insert": f"INSERT INTO {qualified} VALUES(2,'insert',2.000000001)",
        "update": f"UPDATE {qualified} SET label='updated' WHERE id=2",
        "delete": f"DELETE FROM {qualified} WHERE id=2",
        "restart": f"INSERT INTO {qualified} VALUES(3,'after-restart',3)",
    }[operation]
    if kind == "sqlserver":
        return await asyncio.to_thread(sqlserver_sql, sql)
    return await maria_change(table, sql)


def document(event, field="after"):
    value = event.get(field) or {}
    return json.loads(value) if isinstance(value, str) else value


async def postgres_fixture(table, password, prepare=False):
    import asyncpg

    connection = await asyncpg.connect(
        host="127.0.0.1",
        port=5434,
        user="postgres",
        password=password,
        database="cluecdc_destination",
        timeout=10,
    )
    try:
        if prepare:
            await connection.execute(
                f'CREATE TABLE public."{table}" (id INT PRIMARY KEY, label TEXT, amount NUMERIC(30,9))'
            )
        return {
            r["id"]: dict(r)
            for r in await connection.fetch(f'SELECT * FROM public."{table}"')
        }
    finally:
        await connection.close()


async def existing_fixture(kind, table, env, operation="setup"):
    password = (
        env.get("POSTGRES_DEMO_ADMIN_PASSWORD", "postgres123")
        if kind == "postgresql"
        else env.get("MYSQL_DEMO_ADMIN_PASSWORD", "mysql123")
    )
    qualified = f'public."{table}"' if kind == "postgresql" else f"`{table}`"
    statements = {
        "setup": [
            f"CREATE TABLE {qualified} (id INT PRIMARY KEY,label VARCHAR(255),amount DECIMAL(30,9))",
            f"INSERT INTO {qualified} VALUES (1,'Ti\u1ebfng Vi\u1ec7t',12345678901234567890.123456789)",
        ],
        "insert": [f"INSERT INTO {qualified} VALUES (2,'insert',2.000000001)"],
        "update": [f"UPDATE {qualified} SET label='updated' WHERE id=2"],
        "delete": [f"DELETE FROM {qualified} WHERE id=2"],
        "restart": [f"INSERT INTO {qualified} VALUES (3,'after-restart',3)"],
    }[operation]
    if kind == "postgresql":
        import asyncpg

        connection = await asyncpg.connect(
            host="127.0.0.1",
            port=5434,
            user="postgres",
            password=password,
            database="cluecdc_source",
        )
        try:
            for statement in statements:
                await connection.execute(statement)
        finally:
            await connection.close()
    else:
        import aiomysql

        connection = await aiomysql.connect(
            host="127.0.0.1",
            port=3307,
            user="root",
            password=password,
            db="cluecdc_source",
            autocommit=True,
            charset="utf8mb4",
        )
        try:
            async with connection.cursor() as cursor:
                for statement in statements:
                    await cursor.execute(statement)
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=["postgresql", "mysql", "mariadb", "sqlserver", "mongodb"],
        required=True,
    )
    parser.add_argument("--connect-url", default="http://localhost:8083")
    parser.add_argument("--kafka-bootstrap", default="kafka:29092")
    parser.add_argument("--source-host", default=None)
    parser.add_argument("--minio-url", default="http://localhost:9000")
    parser.add_argument("--minio-internal-url", default="http://minio:9000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--outage-container",
        help="Restart only a disposable cluecdc-source-fixtures container twice",
    )
    parser.add_argument(
        "--stateful-postgres",
        action="store_true",
        help="Also verify MariaDB state in the existing PostgreSQL test destination",
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    from cdc_acceptance_support import load_fixture_environment

    env = load_fixture_environment([args.env_file] if args.env_file.exists() else [])
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("AUTH_MODE", "developer")
    os.environ.setdefault("ENVIRONMENT", "test")
    for key in ("CONNECT_SECRET_TOKEN", "SECRET_ENCRYPTION_KEY"):
        if key in env:
            os.environ.setdefault(key, env[key])
    access = os.getenv("MINIO_ACCESS_KEY", env.get("MINIO_ACCESS_KEY", "minioadmin"))
    secret = os.getenv("MINIO_SECRET_KEY", env.get("MINIO_SECRET_KEY", "minioadmin"))
    run = uuid.uuid4().hex[:12]
    table, prefix, bucket = "cdc_" + run, "source_e2e_" + run, "source-e2e-" + run
    s3 = boto3.client(
        "s3",
        endpoint_url=args.minio_url,
        region_name="us-east-1",
        aws_access_key_id=access,
        aws_secret_access_key=secret,
    )
    s3.create_bucket(Bucket=bucket)
    old = args.source in {"postgresql", "mysql"}
    if old:
        asyncio.run(existing_fixture(args.source, table, env))
    else:
        asyncio.run(
            {
                "mariadb": maria_setup,
                "sqlserver": sqlserver_setup,
                "mongodb": mongo_setup,
            }[args.source](table)
        )
    host = args.source_host or (
        {"postgresql": "postgres-demo", "mysql": "mysql-demo"}.get(args.source)
        or f"cluecdc-source-fixtures-{args.source}-source-1"
    )
    schema = {
        "postgresql": "public",
        "mysql": "cluecdc_source",
        "sqlserver": "dbo",
    }.get(args.source, "inventory")
    database_name = "cluecdc_source" if old else "inventory"
    port = {
        "postgresql": 5432,
        "mysql": 3306,
        "mariadb": 3306,
        "sqlserver": 1433,
        "mongodb": 27017,
    }[args.source]
    local_port = {
        "postgresql": 5434,
        "mysql": 3307,
        "mariadb": 13306,
        "sqlserver": 11433,
        "mongodb": 17017,
    }[args.source]
    username = {"postgresql": "postgres", "mysql": "root", "sqlserver": "sa"}.get(
        args.source, "cdc"
    )
    password = {
        "postgresql": env.get("POSTGRES_DEMO_ADMIN_PASSWORD", "postgres123"),
        "mysql": env.get("MYSQL_DEMO_ADMIN_PASSWORD", "mysql123"),
        "sqlserver": "Local-CDC-Fixture-Password-123!",
    }.get(args.source, "local-cdc-fixture-password")

    def mutate(operation):
        asyncio.run(
            existing_fixture(args.source, table, env, operation)
            if old
            else change(args.source, table, operation)
        )

    source = Source(
        name="e2e",
        type=args.source,
        host=host,
        port=port,
        database_name=database_name,
        username=username,
        ssl_enabled=False,
        provider_options={"server_id": int(run[:6], 16) + 700000000},
    )
    local = Source(
        name="e2e",
        type=args.source,
        host="127.0.0.1",
        port=local_port,
        database_name=database_name,
        username=username,
        ssl_enabled=False,
        provider_options={},
    )
    local_secret = (
        "mongodb://127.0.0.1:17017/?directConnection=true&replicaSet=cluecdc-rs"
        if args.source == "mongodb"
        else password
    )
    adapter = get_provider(args.source).source_adapter(local, local_secret)
    readiness = asyncio.run(adapter.readiness())
    assert readiness.get("success", readiness.get("status") != "failed"), (
        "Source readiness failed"
    )
    discovered = asyncio.run(adapter.discover())
    assert any(
        t["table_name"] == table
        and t["primary_key_columns"]
        == (["_id"] if args.source == "mongodb" else ["id"])
        for t in discovered
    )
    data = PipelineInput(
        name="e2e",
        source_id=uuid.uuid4(),
        kafka_cluster_id=uuid.uuid4(),
        connect_cluster_id=uuid.uuid4(),
        topic_prefix=prefix,
        tables=[TableSelection(schema_name=schema, table_name=table)],
    )
    name = "source-e2e-" + run
    config = get_provider(args.source).build_source_config(
        source,
        data,
        f"mongodb://{host}:27017/?replicaSet=cluecdc-rs"
        if args.source == "mongodb"
        else password,
        name,
        args.kafka_bootstrap,
    )
    from app.providers.source_capabilities import source_topic

    topic = source_topic(source, prefix, schema, table)
    delivery = DeliveryInput(
        pipeline_id=uuid.uuid4(),
        delivery_type="OBJECT_STORAGE",
        topics=[topic],
        compression="none",
        file_max_records=1,
        flush_interval_ms=1000,
    )
    destination = Connection(
        name="e2e",
        category="OBJECT_STORAGE",
        provider="MINIO",
        secret_ref=uuid.uuid4(),
        config_json={
            "bucket": bucket,
            "region": "us-east-1",
            "endpoint": args.minio_internal_url,
        },
    )
    sink_name = "sink-e2e-" + run
    sink = ObjectStorageDeliveryProvider().build_config(
        destination, delivery, {}, sink_name
    )
    sink["aws.access.key.id"] = access
    sink["aws.secret.access.key"] = secret
    configs = [config, sink]
    pg_password = env.get("POSTGRES_DEMO_ADMIN_PASSWORD", "postgres123")
    pg_rows = lambda: asyncio.run(postgres_fixture(table, pg_password))
    if args.stateful_postgres:
        if args.source != "mariadb":
            raise RuntimeError("Stateful verification is enabled only for MariaDB")
        from app.providers.types import analyze_columns
        from app.schemas.requests import TopicMapping
        from app.services.destinations.config import DestinationConfigBuilder

        asyncio.run(postgres_fixture(table, pg_password, prepare=True))
        info = asyncio.run(adapter.inspect_table(schema, table))
        jdbc_data = DeliveryInput(
            pipeline_id=data.source_id,
            mappings=[
                TopicMapping(topic=topic, schema_name="public", table_name=table)
            ],
            auto_create=False,
            auto_evolve=False,
        )
        jdbc_destination = Source(
            name="e2e-destination",
            type="postgresql",
            host="postgres-demo",
            port=5432,
            database_name="cluecdc_destination",
            username="postgres",
            ssl_enabled=False,
            provider_options={},
            secret_ref=uuid.uuid4(),
        )
        metadata = {
            topic: {
                "schema_name": "public",
                "table_name": table,
                "effective_keys": ["id"],
                "primary_keys": ["id"],
                "key_mode": "PRIMARY_KEY",
                "columns": analyze_columns(info["columns"], args.source, "postgresql"),
            }
        }
        jdbc_config = DestinationConfigBuilder().build(
            jdbc_destination, jdbc_data, metadata, "jdbc-e2e-" + run
        )
        jdbc_config["connection.password"] = pg_password
        configs.append(jdbc_config)

    def records():
        result = []
        for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
            raw = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            if obj["Key"].endswith(".gz"):
                raw = gzip.decompress(raw)
            for line in raw.splitlines():
                record = json.loads(line)
                value = record.get("value")
                if isinstance(value, str):
                    value = json.loads(value)
                if isinstance(value, dict):
                    result.append(value)
        return result

    with httpx.Client(base_url=args.connect_url, timeout=90) as client:
        for cfg in configs:
            response = client.put(
                "/connector-plugins/" + cfg["connector.class"] + "/config/validate",
                json=cfg,
            )
            response.raise_for_status()
            validation = response.json()
            errors = [
                c["value"]["name"]
                for c in validation.get("configs", [])
                if c["value"].get("errors")
            ]
            assert not validation["error_count"], "Invalid fields: " + ", ".join(errors)
        # Only the unique connectors below are eligible for cleanup.
        created = []
        try:
            for cfg in configs:
                response = client.post(
                    "/connectors", json={"name": cfg["name"], "config": cfg}
                )
                response.raise_for_status()
                created.append(cfg["name"])
            wait_for(
                lambda: any(r.get("op") == "r" for r in records()),
                "snapshot in destination",
            )
            if args.stateful_postgres:
                wait_for(lambda: pg_rows().get(1), "PostgreSQL snapshot")
            mutate("insert")
            if args.stateful_postgres:
                wait_for(
                    lambda: pg_rows().get(2, {}).get("label") == "insert",
                    "PostgreSQL insert",
                )
            mutate("update")
            if args.stateful_postgres:
                wait_for(
                    lambda: pg_rows().get(2, {}).get("label") == "updated",
                    "PostgreSQL update",
                )
            mutate("delete")
            if args.stateful_postgres:
                wait_for(lambda: 2 not in pg_rows(), "PostgreSQL delete")
            wait_for(
                lambda: {"c", "u", "d"} <= {r.get("op") for r in records()},
                "INSERT/UPDATE/DELETE in destination",
            )
            if args.source != "mongodb":
                assert any(
                    r.get("op") == "u" and document(r).get("label") == "updated"
                    for r in records()
                )
            else:
                update = next(r for r in records() if r.get("op") == "u")
                assert (
                    update.get("after") is None
                    and update.get("updateDescription") is not None
                )
            snapshot = next(r for r in records() if r.get("op") == "r")
            assert document(snapshot)["label"] == "Ti\u1ebfng Vi\u1ec7t"
            if args.source == "mongodb":
                assert document(snapshot)["nested"] == {"array": [1, "a", None, True]}
                assert "$numberDecimal" in document(snapshot)["amount"]
                from bson import Decimal128, ObjectId, json_util

                bson_document = json_util.loads(snapshot["after"])
                assert isinstance(bson_document["_id"], ObjectId)
                assert isinstance(bson_document["amount"], Decimal128)
                assert str(bson_document["amount"]) == "12345678901234567890.123456789"
                assert bytes(bson_document["binary"]) == b"\x00\xff"
                assert (
                    bson_document["date"].isoformat().startswith("2026-10-30T00:00:00")
                )
            else:
                import base64
                from decimal import Decimal, localcontext

                with localcontext() as decimal_context:
                    decimal_context.prec = 50
                    raw_amount = document(snapshot)["amount"]
                    exact = Decimal(
                        int.from_bytes(base64.b64decode(raw_amount), "big", signed=True)
                    ).scaleb(-9)
                    assert str(exact) == "12345678901234567890.123456789"
            if args.stateful_postgres:
                assert pg_rows()[1]["label"] == document(snapshot)["label"]
                assert str(pg_rows()[1]["amount"]) == "12345678901234567890.123456789"
            for operation in ("pause", "resume"):
                response = client.put(f"/connectors/{name}/{operation}")
                response.raise_for_status()
                expected = "PAUSED" if operation == "pause" else "RUNNING"
                wait_for(
                    lambda expected=expected: (
                        client.get(f"/connectors/{name}/status").json()["connector"][
                            "state"
                        ]
                        == expected
                    ),
                    operation,
                )
            client.post(
                f"/connectors/{name}/restart?includeTasks=true&onlyFailed=false"
            ).raise_for_status()
            mutate("restart")
            wait_for(
                lambda: any(
                    document(r).get("label") == "after-restart" for r in records()
                ),
                "capture after connector restart",
            )
            recovery = "NOT_RUN"
            if args.outage_container:
                expected_container = f"cluecdc-source-fixtures-{args.source}-source-1"
                if args.outage_container != expected_container:
                    raise RuntimeError(
                        "Outage tests are restricted to named disposable source fixtures"
                    )
                for cycle in range(2):
                    offsets = client.get(f"/connectors/{name}/offsets").json()
                    assert offsets.get("offsets"), (
                        "No durable source offsets before outage"
                    )
                    subprocess.run(
                        ["docker", "restart", args.outage_container],
                        check=True,
                        capture_output=True,
                        timeout=120,
                    )

                    def available():
                        try:
                            return asyncio.run(adapter.test()).get("success")
                        except DomainError:
                            return False

                    wait_for(available, "source recovery", timeout=180)
                    # Retry a failed task using the existing lifecycle, never recreate
                    # the connector or change its saved configuration/offsets.
                    client.post(
                        f"/connectors/{name}/restart?includeTasks=true&onlyFailed=true"
                    ).raise_for_status()
                    marker = f"outage-{cycle}"
                    if args.source == "mongodb":
                        asyncio.run(mongo_change(table, marker))
                    else:
                        qualified = (
                            f"dbo.[{table}]"
                            if args.source == "sqlserver"
                            else f"`{table}`"
                        )
                        sql = f"INSERT INTO {qualified} VALUES({10 + cycle},'outage-{cycle}',1)"
                        if args.source == "sqlserver":
                            sqlserver_sql(sql)
                        else:
                            asyncio.run(maria_change(table, sql))
                    wait_for(
                        lambda marker=marker: any(
                            document(record).get("label") == marker
                            for record in records()
                        ),
                        "destination after source outage",
                    )
                    assert (
                        client.get(f"/connectors/{name}/offsets").json().get("offsets")
                    )
                recovery = "PASS_WITH_EXISTING_TASK_RESTART"
            evidence = {
                "source": args.source,
                "run": run,
                "connector": name,
                "topic": topic,
                "bucket": bucket,
                "source_version": readiness.get("version"),
                "resume_settings": {
                    key: readiness.get("gtid", {}).get(key)
                    for key in ("binlog_legacy_event_pos", "log_bin_compress")
                }
                if args.source == "mariadb"
                else None,
                "readiness": "PASS",
                "discovery": "PASS",
                "snapshot": "PASS",
                "insert": "PASS",
                "update": "PASS",
                "delete": "PASS",
                "unicode": "PASS",
                "pause_resume": "PASS",
                "connector_restart": "PASS",
                "destination": "MinIO raw JSONL",
                "stateful_delivery": "PASS_POSTGRESQL_CRUD_DECIMAL_UNICODE"
                if args.stateful_postgres
                else "NOT_RUN",
                "outage_recovery": recovery,
                "records_verified": len(records()),
                "decimal_precision": "PASS",
                "bson_nested": "PASS" if args.source == "mongodb" else "NOT_RUN",
            }
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(evidence, indent=2) + "\n")
            print(json.dumps(evidence))
        finally:
            for owned in reversed(created):
                client.delete("/connectors/" + owned).raise_for_status()


if __name__ == "__main__":
    main()
