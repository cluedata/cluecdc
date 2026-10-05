"""Verify real PostgreSQL CDC envelopes in Kafka Connect-generated MinIO JSONL objects."""

import argparse
import gzip
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import boto3

ROOT = pathlib.Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--api-url", default="http://localhost:8000/api/v1")
parser.add_argument("--minio-url", default="http://localhost:9000")
parser.add_argument("--compose-project")
args = parser.parse_args()
env = dict(
    line.split("=", 1)
    for line in (ROOT / ".env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
access_key = os.getenv(
    "MINIO_ACCESS_KEY", env.get("MINIO_ACCESS_KEY", "cluecdc-test-access")
)
secret_key = os.getenv(
    "MINIO_SECRET_KEY", env.get("MINIO_SECRET_KEY", "cluecdc-test-secret-key")
)
source_password = env["SOURCE_PASSWORD"]


def api(path, method="GET", data=None):
    headers = {"Content-Type": "application/json"}
    if os.getenv("CLUECDC_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["CLUECDC_TOKEN"]
    request = urllib.request.Request(
        args.api_url + path,
        method=method,
        headers=headers,
        data=json.dumps(data).encode() if data is not None else None,
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode()
            assert all(
                value not in raw for value in (access_key, secret_key, source_password)
            )
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        raise RuntimeError(f"{method} {path}: {payload['error']['code']}") from None


def wait_for(fn, predicate, label, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = fn()
        if predicate(result):
            return result
        time.sleep(1)
    raise RuntimeError("Timed out waiting for " + label)


def find_or_create(path, name, data):
    return next((item for item in api(path) if item["name"] == name), None) or api(
        path, "POST", {"name": name, **data}
    )


source = find_or_create(
    "/connections",
    "Object storage acceptance source",
    {
        "provider": "POSTGRESQL",
        "category": "DATABASE",
        "capabilities": ["SOURCE"],
        "config": {
            "host": "cdc-source-postgres",
            "port": 5432,
            "database_name": "commerce",
            "username": "cdc_user",
        },
        "credentials": {"password": source_password},
    },
)
job = api(f"/sources/{source['id']}/discover", "POST")
job = wait_for(
    lambda: api(f"/jobs/{job['id']}"),
    lambda item: item["status"] in {"COMPLETED", "FAILED"},
    "source discovery",
)
assert job["status"] == "COMPLETED", "Source discovery failed"
kafka = find_or_create(
    "/kafka/clusters", "Local Kafka", {"bootstrap_servers": "kafka:29092"}
)
connect = find_or_create(
    "/connect/clusters",
    "Local Connect",
    {"base_url": "http://kafka-connect:8083", "kafka_cluster_id": kafka["id"]},
)
pipeline = find_or_create(
    "/pipelines",
    "Object storage acceptance capture",
    {
        "source_id": source["id"],
        "kafka_cluster_id": kafka["id"],
        "connect_cluster_id": connect["id"],
        "topic_prefix": "object_storage_acceptance",
        "snapshot_mode": "initial",
        "tables": [{"schema_name": "public", "table_name": "customers"}],
    },
)
if not pipeline["connector_id"]:
    api(f"/pipelines/{pipeline['id']}/deploy", "POST")
wait_for(
    lambda: api(f"/pipelines/{pipeline['id']}/status"),
    lambda item: (
        bool(item.get("tasks"))
        and all(task["state"] == "RUNNING" for task in item["tasks"])
    ),
    "Debezium capture",
)
marker = uuid.uuid4().hex
prefix = "acceptance/" + marker
destination = api(
    "/connections",
    "POST",
    {
        "name": "MinIO acceptance " + marker[:8],
        "category": "OBJECT_STORAGE",
        "provider": "MINIO",
        "config": {
            "endpoint": "http://minio:9000",
            "bucket": "cluecdc-cdc",
            "region": "us-east-1",
            "prefix": prefix,
            "use_ssl": False,
            "path_style_access": True,
        },
        "credentials": {"access_key": access_key, "secret_key": secret_key},
    },
)
assert api(f"/connections/{destination['id']}/test", "POST")["success"]
options = {
    "pipeline_id": pipeline["id"],
    "name": "JSONL acceptance " + marker[:8],
    "delivery_type": "OBJECT_STORAGE",
    "topics": ["object_storage_acceptance.public.customers"],
    "compression": "gzip",
    "file_max_records": 1,
    "flush_interval_ms": 1000,
}
preview = api(f"/destinations/{destination['id']}/preview", "POST", options)
assert "transforms" not in preview["config"], (
    "JDBC transformation must not be used for S3"
)
delivery = api(f"/destinations/{destination['id']}/deploy", "POST", options)
wait_for(
    lambda: api(f"/deliveries/{delivery['id']}/status"),
    lambda item: (
        bool(item.get("tasks"))
        and all(task["state"] == "RUNNING" for task in item["tasks"])
    ),
    "S3 sink",
)

# Each statement commits independently, producing actual Debezium c/u/d envelopes.
email = marker + "@object-storage.test"
sql = (
    f"INSERT INTO customers(name, email) VALUES ('create {marker}', '{email}');\n"
    f"UPDATE customers SET name = 'update {marker}', updated_at = now() WHERE email = '{email}';\n"
    f"DELETE FROM customers WHERE email = '{email}';\n"
)
command = ["docker", "compose"]
if args.compose_project:
    command.extend(["-p", args.compose_project])
command.extend(
    [
        "-f",
        "compose.yaml",
        "-f",
        "compose.test.yaml",
        "exec",
        "-T",
        "cdc-source-postgres",
        "psql",
        "-U",
        "postgres",
        "-d",
        "commerce",
        "-v",
        "ON_ERROR_STOP=1",
    ]
)
result = subprocess.run(
    command, input=sql, text=True, cwd=ROOT, capture_output=True, check=False
)
assert result.returncode == 0, "Source CDC mutations failed"
client = boto3.client(
    "s3",
    endpoint_url=args.minio_url,
    region_name="us-east-1",
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
)


def exported():
    matches = {}
    keys = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket="cluecdc-cdc", Prefix=prefix + "/"
    ):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            assert "/year=" in key and "/month=" in key and "/day=" in key
            assert key.endswith(".jsonl.gz"), "Expected compressed JSONL output"
            keys.append(key)
            body = client.get_object(Bucket="cluecdc-cdc", Key=key)["Body"]
            try:
                records = gzip.decompress(body.read()).decode().splitlines()
            finally:
                body.close()
            for line in records:
                record = json.loads(line)
                event = record.get("value")
                if not isinstance(event, dict):
                    continue
                before, after = event.get("before"), event.get("after")
                row = after or before or {}
                if row.get("email") != email:
                    continue
                assert event["source"]["db"] == "commerce"
                assert event["source"]["schema"] == "public"
                assert event["source"]["table"] == "customers"
                assert event.get("ts_ms") is not None
                assert record["key"]["id"] == row["id"]
                if event["op"] == "c":
                    assert before is None and after["name"] == "create " + marker
                elif event["op"] == "u":
                    assert before["name"] == "create " + marker
                    assert after["name"] == "update " + marker
                elif event["op"] == "d":
                    assert before["name"] == "update " + marker and after is None
                matches[event["op"]] = row["id"]
    return matches, keys


try:
    operations, object_keys = wait_for(
        exported, lambda item: {"c", "u", "d"} <= set(item[0]), "CDC object contents"
    )
    assert len(set(operations.values())) == 1
    report = {
        "verified": True,
        "connector_class": preview["connector_class"],
        "format": "JSONL",
        "compression": "gzip",
        "operations": sorted(operations),
        "objects": object_keys,
        "delivery_id": delivery["id"],
    }
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts/object-storage-verification.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print("Verified real CDC inserts, updates and deletes in MinIO gzip JSONL objects.")
finally:
    client.close()
