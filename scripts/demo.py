"""Reproducible live PostgreSQL -> Debezium -> Kafka -> ClueCDC smoke test."""

import argparse
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode

root = pathlib.Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--api-url", default="http://localhost:8000/api/v1")
args = parser.parse_args()
secrets = dict(
    line.split("=", 1)
    for line in (root / ".env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)


def request(path, method="GET", data=None):
    headers = {"Content-Type": "application/json"}
    if os.environ.get("CLUECDC_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["CLUECDC_TOKEN"]
    req = urllib.request.Request(
        args.api_url + path,
        method=method,
        headers=headers,
        data=json.dumps(data).encode() if data is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=50) as response:
            raw = response.read().decode()
            assert secrets["SOURCE_PASSWORD"] not in raw, (
                "Source credential leaked in API response"
            )
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        raise RuntimeError(
            f"{method} {path}: {payload['error']['code']} {payload['error']['message']}"
        ) from None


def find_or_create(path, name, data):
    existing = next(
        (entity for entity in request(path) if entity["name"] == name), None
    )
    return existing or request(path, "POST", {"name": name, **data})


def wait_for(fn, predicate, label, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = fn()
        if predicate(result):
            return result
        time.sleep(1)
    raise RuntimeError("Timed out waiting for " + label)


source = find_or_create(
    "/sources",
    "Commerce PostgreSQL",
    {
        "type": "postgresql",
        "host": "cdc-source-postgres",
        "port": 5432,
        "database_name": "commerce",
        "username": "cdc_user",
        "password": secrets["SOURCE_PASSWORD"],
    },
)
request(f"/sources/{source['id']}/test", "POST")
readiness = request(f"/sources/{source['id']}/cdc-readiness")
assert readiness["status"] != "failed", "CDC readiness checks failed"
job = request(f"/sources/{source['id']}/discover", "POST")
job = wait_for(
    lambda: request(f"/jobs/{job['id']}"),
    lambda j: j["status"] in {"COMPLETED", "FAILED"},
    "discovery",
)
assert job["status"] == "COMPLETED", job.get("error")
tables = request(f"/sources/{source['id']}/tables")
assert all(
    next(t for t in tables if t["table_name"] == name)["cdc_ready"]
    for name in ["customers", "orders"]
)
kafka = find_or_create(
    "/kafka/clusters", "Local Kafka", {"bootstrap_servers": "kafka:29092"}
)
connect = find_or_create(
    "/connect/clusters",
    "Local Connect",
    {"base_url": "http://kafka-connect:8083", "kafka_cluster_id": kafka["id"]},
)
data = {
    "name": "Commerce capture",
    "source_id": source["id"],
    "kafka_cluster_id": kafka["id"],
    "connect_cluster_id": connect["id"],
    "topic_prefix": "commerce_demo",
    "snapshot_mode": "initial",
    "tables": [
        {"schema_name": "public", "table_name": name}
        for name in ["customers", "orders"]
    ],
}
preview = request("/pipelines/preview", "POST", data)
assert preview["config"]["database.password"] == "[REDACTED]"
p = next(
    (p for p in request("/pipelines") if p["name"] == data["name"]), None
) or request("/pipelines", "POST", data)
customer_topic = f"{p['topic_prefix']}.public.customers"
if not p["connector_id"]:
    request(f"/pipelines/{p['id']}/deploy", "POST")
else:
    request(f"/pipelines/{p['id']}/resume", "POST")
wait_for(
    lambda: request(f"/pipelines/{p['id']}/status"),
    lambda s: (
        s.get("connector", {}).get("state") == "RUNNING"
        and bool(s.get("tasks"))
        and all(task.get("state") == "RUNNING" for task in s["tasks"])
    ),
    "running connector",
)
print(
    "Source tested; readiness checked; tables discovered; connector deployed and RUNNING."
)
marker = "CDC demo " + uuid.uuid4().hex
sql = (root / "scripts/generate-changes.sql").read_text().replace("CDC demo", marker)
result = subprocess.run(
    [
        "docker",
        "compose",
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
    ],
    input=sql,
    text=True,
    cwd=root,
    capture_output=True,
    check=False,
)
if result.returncode:
    raise RuntimeError("Sample source mutations failed")


def current_events(sample):
    return [
        event
        for event in sample["events"]
        if any(
            str((event.get(side) or {}).get("name", "")).startswith(marker)
            for side in ["before", "after"]
        )
    ]


sample = wait_for(
    lambda: request(
        "/events?"
        + urlencode({"cluster_id": kafka["id"], "topic": customer_topic, "limit": 200})
    ),
    lambda s: {"CREATE", "UPDATE", "DELETE"}.issubset(
        {e["operation"] for e in current_events(s)}
    ),
    "this run's CDC events",
)
update = next(e for e in current_events(sample) if e["operation"] == "UPDATE")
assert update["before"] and update["after"] and "name" in update["changed_fields"]
print(
    "Real CREATE, UPDATE, DELETE events observed in Kafka; UPDATE contains before/after."
)
for operation, state in [
    ("pause", "PAUSED"),
    ("resume", "RUNNING"),
    ("restart", "RUNNING"),
]:
    request(f"/pipelines/{p['id']}/{operation}", "POST")
    wait_for(
        lambda: request(f"/pipelines/{p['id']}/status"),
        lambda s, state=state: (
            s.get("connector", {}).get("state") == state
            and bool(s.get("tasks"))
            and all(task.get("state") == state for task in s["tasks"])
        ),
        operation,
    )
    print(f"{operation}: source connector and tasks {state}")
audit = request("/audit?resource_id=" + p["id"])
assert all(
    action in {a["action"] for a in audit}
    for action in [
        "pipeline.created",
        "pipeline.deployed",
        "pipeline.paused",
        "pipeline.resumed",
        "pipeline.restarted",
    ]
)
topics = request("/kafka/topics?cluster_id=" + kafka["id"])
assert customer_topic in {t["name"] for t in topics}
print(f"Verified topic metadata and {len(audit)} pipeline audit entries.")
artifact = {
    "pipeline_id": p["id"],
    "source_id": source["id"],
    "topics": [
        f"{p['topic_prefix']}.public.{table}" for table in ["customers", "orders"]
    ],
    "operations": sorted({e["operation"] for e in current_events(sample)}),
    "bounded_scanned": sample["scanned"],
    "audit_actions": sorted({a["action"] for a in audit}),
}
(root / "artifacts").mkdir(exist_ok=True)
(root / "artifacts/demo-result.json").write_text(json.dumps(artifact, indent=2))
print("Live CDC smoke test PASSED. Open http://localhost:3000/pipelines/" + p["id"])
