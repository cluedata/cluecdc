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
import sys
import time
import uuid
from pathlib import Path

import boto3
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.models.entities import Connection, Source  # noqa: E402
from app.providers import get_provider  # noqa: E402
from app.schemas.requests import DeliveryInput, PipelineInput, TableSelection  # noqa: E402
from app.services.destinations.providers import ObjectStorageDeliveryProvider  # noqa: E402


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

    connection = await aiomysql.connect(host="127.0.0.1", port=13306, user="root",
        password="local-cdc-fixture-password", db="inventory", autocommit=True, charset="utf8mb4")
    async with connection.cursor() as cursor:
        await cursor.execute("GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, "
                             "BINLOG MONITOR, LOCK TABLES ON *.* TO 'cdc'@'%'")
        await cursor.execute(f"CREATE TABLE `{table}` (id INT PRIMARY KEY, "
                             "label VARCHAR(255), amount DECIMAL(30,9))")
        await cursor.execute(f"INSERT INTO `{table}` VALUES (1,'Tiếng Việt',12345678901234567890.123456789)")
    connection.close()


async def maria_change(table, sql):
    import aiomysql

    connection = await aiomysql.connect(host="127.0.0.1", port=13306, user="root",
        password="local-cdc-fixture-password", db="inventory", autocommit=True, charset="utf8mb4")
    async with connection.cursor() as cursor:
        await cursor.execute(sql.format(table=table))
    connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["mariadb"], required=True)
    parser.add_argument("--connect-url", default="http://localhost:8083")
    parser.add_argument("--kafka-bootstrap", default="kafka:29092")
    parser.add_argument("--source-host", default="cluecdc-source-fixtures-mariadb-source-1")
    parser.add_argument("--minio-url", default="http://localhost:9000")
    parser.add_argument("--minio-internal-url", default="http://minio:9000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
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
    s3 = boto3.client("s3", endpoint_url=args.minio_url, region_name="us-east-1",
        aws_access_key_id=access,
        aws_secret_access_key=secret)
    s3.create_bucket(Bucket=bucket)
    asyncio.run(maria_setup(table))
    source = Source(name="e2e", type=args.source, host=args.source_host, port=3306,
        database_name="inventory", username="cdc", ssl_enabled=False,
        provider_options={"server_id": int(run[:6], 16) + 700000000})
    local = Source(name="e2e", type=args.source, host="127.0.0.1", port=13306,
        database_name="inventory", username="cdc", ssl_enabled=False, provider_options={})
    adapter = get_provider(args.source).source_adapter(local, "local-cdc-fixture-password")
    readiness = asyncio.run(adapter.readiness())
    assert readiness["success"], "Source readiness failed"
    discovered = asyncio.run(adapter.discover())
    assert any(t["table_name"] == table and t["primary_key_columns"] == ["id"] for t in discovered)
    data = PipelineInput(name="e2e", source_id=uuid.uuid4(), kafka_cluster_id=uuid.uuid4(),
        connect_cluster_id=uuid.uuid4(), topic_prefix=prefix,
        tables=[TableSelection(schema_name="inventory", table_name=table)])
    name = "source-e2e-" + run
    config = get_provider(args.source).build_source_config(source, data,
        "local-cdc-fixture-password", name, args.kafka_bootstrap)
    topic = f"{prefix}.inventory.{table}"
    delivery = DeliveryInput(pipeline_id=uuid.uuid4(), delivery_type="OBJECT_STORAGE",
        topics=[topic], compression="none", file_max_records=1, flush_interval_ms=1000)
    destination = Connection(name="e2e", category="OBJECT_STORAGE", provider="MINIO",
        secret_ref=uuid.uuid4(), config_json={"bucket": bucket, "region": "us-east-1",
        "endpoint": args.minio_internal_url})
    sink_name = "sink-e2e-" + run
    sink = ObjectStorageDeliveryProvider().build_config(destination, delivery, {}, sink_name)
    sink["aws.access.key.id"] = access
    sink["aws.secret.access.key"] = secret

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
        for cfg in (config, sink):
            response = client.put("/connector-plugins/" + cfg["connector.class"] + "/config/validate", json=cfg)
            response.raise_for_status()
            validation = response.json()
            errors = [c["value"]["name"] for c in validation.get("configs", []) if c["value"].get("errors")]
            assert not validation["error_count"], "Invalid fields: " + ", ".join(errors)
        # Only the unique connectors below are eligible for cleanup.
        created = []
        try:
            for cfg in (config, sink):
                response = client.post("/connectors", json={"name": cfg["name"], "config": cfg})
                response.raise_for_status()
                created.append(cfg["name"])
            wait_for(lambda: any(r.get("op") == "r" for r in records()), "snapshot in destination")
            asyncio.run(maria_change(table, "INSERT INTO `{table}` VALUES (2,'insert',2.000000001)"))
            asyncio.run(maria_change(table, "UPDATE `{table}` SET label='updated' WHERE id=2"))
            asyncio.run(maria_change(table, "DELETE FROM `{table}` WHERE id=2"))
            wait_for(lambda: {"c", "u", "d"} <= {r.get("op") for r in records()},
                     "INSERT/UPDATE/DELETE in destination")
            assert any(r.get("op") == "u" and (r.get("after") or {}).get("label") == "updated"
                       for r in records())
            snapshot = next(r for r in records() if r.get("op") == "r")
            assert snapshot["after"]["label"] == "Tiếng Việt"
            for operation in ("pause", "resume"):
                response = client.put(f"/connectors/{name}/{operation}")
                response.raise_for_status()
                expected = "PAUSED" if operation == "pause" else "RUNNING"
                wait_for(lambda: client.get(f"/connectors/{name}/status").json()["connector"]["state"] == expected,
                         operation)
            client.post(f"/connectors/{name}/restart?includeTasks=true&onlyFailed=false").raise_for_status()
            asyncio.run(maria_change(table, "INSERT INTO `{table}` VALUES (3,'after-restart',3)"))
            wait_for(lambda: any((r.get("after") or {}).get("label") == "after-restart" for r in records()),
                     "capture after connector restart")
            evidence = {"source": args.source, "run": run, "connector": name, "topic": topic,
                "bucket": bucket, "readiness": "PASS", "discovery": "PASS", "snapshot": "PASS",
                "insert": "PASS", "update": "PASS", "delete": "PASS", "unicode": "PASS",
                "pause_resume": "PASS", "connector_restart": "PASS", "destination": "MinIO raw JSONL",
                "stateful_delivery": "NOT_RUN", "outage_recovery": "NOT_RUN",
                "records_verified": len(records())}
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(evidence, indent=2) + "\n")
            print(json.dumps(evidence))
        finally:
            for owned in reversed(created):
                client.delete("/connectors/" + owned).raise_for_status()


if __name__ == "__main__":
    main()
