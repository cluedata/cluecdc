"""Verify an isolated physical destination outage without interrupting source capture."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from cdc_reliability_support import api_for, wait_value
from observability_fixture import attach


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    stack = attach(args.fixture)
    api = api_for(stack)
    pipeline = next(
        p for p in api.request("/pipelines") if p["name"] == f"cdc_{stack.run_id}_po_po"
    )
    table = pipeline["name"]
    config = json.loads(stack.path.read_text(encoding="utf-8"))
    service = "observability-destination"
    config["services"][service] = {
        "image": "postgres:17.9",
        "environment": {
            "POSTGRES_USER": "obs",
            "POSTGRES_PASSWORD": "owned-observability-only",
            "POSTGRES_DB": "owned_destination",
        },
        "labels": {"io.cluecdc.reliability.owner": stack.run_id},
        "healthcheck": {
            "test": ["CMD-SHELL", "pg_isready -U obs -d owned_destination"],
            "interval": "2s",
            "timeout": "2s",
            "retries": 30,
        },
        "mem_limit": "256m",
        "cpus": 1.0,
    }
    stack.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    stack.compose("up", "-d", "--wait", service, timeout=120)
    connection = next(
        (
            c
            for c in api.request("/connections")
            if c["name"] == "owned-isolated-destination"
        ),
        None,
    ) or api.create(
        "/connections",
        {
            "name": "owned-isolated-destination",
            "category": "DATABASE",
            "provider": "POSTGRESQL",
            "capabilities": ["DESTINATION"],
            "config": {
                "host": service,
                "port": 5432,
                "database_name": "owned_destination",
                "username": "obs",
                "ssl_enabled": False,
            },
            "credentials": {"password": "owned-observability-only"},
        },
    )
    delivery = next(
        (
            d
            for d in api.request("/deliveries")
            if d["name"] == "isolated-destination-outage"
            and d["pipeline_id"] == pipeline["id"]
        ),
        None,
    ) or api.create(
        f"/destinations/{connection['id']}/deploy",
        {
            "pipeline_id": pipeline["id"],
            "name": "isolated-destination-outage",
            "write_mode": "upsert",
            "mappings": [
                {
                    "topic": table + ".public." + table,
                    "schema_name": "public",
                    "table_name": table,
                }
            ],
            "auto_create": True,
            "auto_evolve": False,
        },
    )
    path = f"/pipelines/{pipeline['id']}/observability"

    def target(o):
        return next(
            (d for d in o["deliveries"] if d["delivery_id"] == delivery["id"]), None
        )

    wait_value(
        lambda: api.request(path),
        lambda o: target(o) and target(o)["backlog"]["status"] == "AVAILABLE",
        "isolated sink initial sampling",
        timeout=90,
    )
    at = datetime.now(UTC).isoformat()
    stack.fault(service, "stop")
    try:
        stack.sql(
            "postgresql",
            f"UPDATE {table} SET label='owned-destination-outage' WHERE tenant=7 AND id=1",
        )
        observed = wait_value(
            lambda: api.request(path),
            lambda o: (
                o["collected_at"] > at
                and o["capture_lag"]["status"] == "AVAILABLE"
                and target(o)
                and (
                    target(o)["connector_state"] == "FAILED"
                    or target(o)["operational_state"] == "FAILED"
                    or "FAILED" in target(o)["task_states"].values()
                    or (target(o)["backlog"]["value"] or 0) > 0
                )
                and all(
                    d["backlog"]["status"] == "AVAILABLE" and d["backlog"]["value"] == 0
                    for d in o["deliveries"]
                    if d["delivery_id"] != delivery["id"]
                )
            ),
            "source remains measured and independent sinks continue",
            timeout=120,
        )
        assert target(observed)["destination_progress"]["value"] is None
    finally:
        stack.fault(service, "start")
        stack.wait_service(service)
    api.create(f"/deliveries/{delivery['id']}/restart", {})
    actual = wait_value(
        lambda: stack.exec(
            service,
            [
                "psql",
                "-U",
                "obs",
                "-d",
                "owned_destination",
                "-qAt",
                "-c",
                f"SELECT label FROM public.{table} WHERE tenant=7 AND id=1",
            ],
        ),
        lambda value: value == "owned-destination-outage",
        "exact recovered destination row",
        timeout=90,
    )
    (args.fixture.parent / "destination-outage.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "separate_source_and_destination": True,
                "observation": observed,
                "recovered_label": actual,
            },
            indent=2,
        )
    )
    print(
        "Isolated destination outage, independent deliveries and exact recovery passed"
    )


if __name__ == "__main__":
    main()
