"""Real capture -> Kafka -> PostgreSQL delivery acceptance, including fan-out."""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

root = pathlib.Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
parser.add_argument("--skip-capture-demo", action="store_true")
args = parser.parse_args()
env = dict(
    line.split("=", 1)
    for line in (root / ".env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
if not args.skip_capture_demo:
    subprocess.run(
        [sys.executable, "-u", "scripts/demo.py", "--api-url", args.api_url],
        cwd=root,
        check=True,
    )


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
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
            assert env["DESTINATION_PASSWORD"] not in raw, (
                "Destination credential exposed; response withheld"
            )
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())["error"]
        raise RuntimeError(
            f"{method} {path}: {payload['code']} {payload['message']}"
        ) from None


def sql(service, database, statement):
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            service,
            "psql",
            "-U",
            "postgres",
            "-d",
            database,
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=statement,
        text=True,
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"SQL acceptance operation failed on {service}; output withheld"
        )
    return result.stdout.strip()


def wait(fn, predicate, label, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if predicate(value):
            return value
        time.sleep(1)
    raise RuntimeError("Timed out waiting for " + label)


pipeline = next(row for row in api("/pipelines") if row["name"] == "Commerce capture")
topic_prefix = pipeline["topic_prefix"]
destinations = []
for name, schema in [
    ("Analytics PostgreSQL", "public"),
    ("Reporting PostgreSQL", "analytics"),
]:
    target = next((row for row in api("/destinations") if row["name"] == name), None)
    if target is None:
        target = api(
            "/destinations",
            "POST",
            {
                "name": name,
                "environment": "DEV",
                "host": "destination-postgres",
                "port": 5432,
                "database_name": "analytics",
                "username": "delivery_user",
                "password": env["DESTINATION_PASSWORD"],
            },
        )
    api(f"/destinations/{target['id']}/test", "POST")
    detail = api(f"/destinations/{target['id']}")
    delivery = next(
        (
            link
            for link in detail["deliveries"]
            if link["pipeline_id"] == pipeline["id"]
        ),
        None,
    )
    if delivery is None:
        payload = {
            "pipeline_id": pipeline["id"],
            "mappings": [
                {
                    "topic": f"{topic_prefix}.public.{table}",
                    "schema_name": schema,
                    "table_name": table,
                }
                for table in ["customers", "orders"]
            ],
        }
        preview = api(f"/destinations/{target['id']}/preview", "POST", payload)
        assert preview["config"]["connection.password"] == "[REDACTED]"
        delivery = api(f"/destinations/{target['id']}/deploy", "POST", payload)
    else:
        api(f"/destinations/{target['id']}/resume", "POST")
    wait(
        lambda target=target: api(f"/destinations/{target['id']}/status"),
        lambda state: state["actual_state"] == "RUNNING",
        name + " sink RUNNING",
    )
    destinations.append((target, schema, delivery))
    print(
        name + ": real connection tested, mappings validated, JDBC sink RUNNING",
        flush=True,
    )

marker = "Delivery " + uuid.uuid4().hex
customer_id = int(
    sql(
        "cdc-source-postgres",
        "commerce",
        f"INSERT INTO customers(name,email) VALUES ('{marker}','{uuid.uuid4().hex}@example.test') RETURNING id;",
    )
)
order_id = int(
    sql(
        "cdc-source-postgres",
        "commerce",
        f"INSERT INTO orders(customer_id,total) VALUES ({customer_id},12.34) RETURNING id;",
    )
)
for target, schema, _ in destinations:
    wait(
        lambda schema=schema: sql(
            "destination-postgres",
            "analytics",
            f"SELECT name FROM {schema}.customers WHERE id={customer_id};",
        ),
        lambda value: value == marker,
        schema + " customer INSERT",
    )
    wait(
        lambda schema=schema: sql(
            "destination-postgres",
            "analytics",
            f"SELECT total FROM {schema}.orders WHERE id={order_id};",
        ),
        lambda value: value == "12.34",
        schema + " numeric order INSERT",
    )
print("Fresh customer and order INSERTs delivered to both mapped schemas.", flush=True)

sql(
    "cdc-source-postgres",
    "commerce",
    f"UPDATE customers SET name='{marker} updated', updated_at=now() WHERE id={customer_id}; UPDATE orders SET total=45.67,status='paid',updated_at=now() WHERE id={order_id};",
)
for target, schema, _ in destinations:
    wait(
        lambda schema=schema: sql(
            "destination-postgres",
            "analytics",
            f"SELECT name FROM {schema}.customers WHERE id={customer_id};",
        ),
        lambda value: value == marker + " updated",
        schema + " customer UPDATE",
    )
    wait(
        lambda schema=schema: sql(
            "destination-postgres",
            "analytics",
            f"SELECT total || ':' || status FROM {schema}.orders WHERE id={order_id};",
        ),
        lambda value: value == "45.67:paid",
        schema + " order UPDATE",
    )
print(
    "UPDATE reflected in both destinations, including decimal and timestamp fields.",
    flush=True,
)

target, schema, _ = destinations[0]
api(f"/destinations/{target['id']}/pause", "POST")
wait(
    lambda: api(f"/destinations/{target['id']}/status"),
    lambda state: state["actual_state"] == "PAUSED",
    "destination PAUSED",
)
paused_id = int(
    sql(
        "cdc-source-postgres",
        "commerce",
        f"INSERT INTO customers(name,email) VALUES ('{marker} paused','{uuid.uuid4().hex}@example.test') RETURNING id;",
    )
)
time.sleep(3)
paused_count = sql(
    "destination-postgres",
    "analytics",
    f"SELECT count(*) FROM public.customers WHERE id={paused_id};",
)
if paused_count != "0":
    raise RuntimeError(
        "The selected delivery is paused, but another Kafka Connect connector is "
        "still writing public.customers. Inspect unmanaged/orphan connectors before "
        "rerunning this isolation check; ClueCDC will not delete them automatically."
    )
capture_status = api(f"/pipelines/{pipeline['id']}/status")
assert capture_status["connector"]["state"] == "RUNNING"
assert capture_status["tasks"] and all(
    task["state"] == "RUNNING" for task in capture_status["tasks"]
)
api(f"/destinations/{target['id']}/resume", "POST")
wait(
    lambda: api(f"/destinations/{target['id']}/status"),
    lambda state: state["actual_state"] == "RUNNING",
    "destination resumed",
)
wait(
    lambda: sql(
        "destination-postgres",
        "analytics",
        f"SELECT count(*) FROM public.customers WHERE id={paused_id};",
    ),
    lambda value: value == "1",
    "paused row delivered after resume",
)
api(f"/destinations/{target['id']}/restart", "POST")
wait(
    lambda: api(f"/destinations/{target['id']}/status"),
    lambda state: state["actual_state"] == "RUNNING",
    "sink restarted",
)
print(
    "Pause held delivery while capture stayed RUNNING; resume caught up; restart succeeded.",
    flush=True,
)

sql(
    "cdc-source-postgres",
    "commerce",
    f"DELETE FROM orders WHERE id={order_id}; DELETE FROM customers WHERE id IN ({customer_id},{paused_id});",
)
for _, schema, _ in destinations:
    wait(
        lambda schema=schema: sql(
            "destination-postgres",
            "analytics",
            f"SELECT count(*) FROM {schema}.customers WHERE id IN ({customer_id},{paused_id});",
        ),
        lambda value: value == "0",
        schema + " DELETE",
    )
    wait(
        lambda schema=schema: sql(
            "destination-postgres",
            "analytics",
            f"SELECT count(*) FROM {schema}.orders WHERE id={order_id};",
        ),
        lambda value: value == "0",
        schema + " order DELETE",
    )
actions = {row["action"] for row in api("/audit?resource_id=" + target["id"])}
assert {
    "destination.created",
    "destination.connection_tested",
    "destination.deployed",
    "destination.paused",
    "destination.resumed",
    "destination.restarted",
}.issubset(actions)
links = api(f"/pipelines/{pipeline['id']}/destinations")
assert len({link["destination_id"] for link in links}) >= 2
(root / "artifacts").mkdir(exist_ok=True)
(root / "artifacts/destination-demo-result.json").write_text(
    json.dumps(
        {
            "pipeline_id": pipeline["id"],
            "destination_ids": [target["id"] for target, _, _ in destinations],
            "operations": ["INSERT", "UPDATE", "DELETE", "PAUSE", "RESUME", "RESTART"],
            "fan_out": True,
            "audit_actions": sorted(actions),
        },
        indent=2,
    )
)
print(
    "Live source -> Kafka -> independent PostgreSQL destinations acceptance PASSED.",
    flush=True,
)
