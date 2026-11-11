"""Retry the failed owned PostgreSQL -> MySQL gate using its preserved source topic."""

import argparse
import json
from pathlib import Path

from cdc_reliability import Scenario
from cdc_reliability_support import Evidence, wait_value
from observability_fixture import attach, trusted_exporters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    stack = attach(args.fixture)
    # Reload only our fixture's API/worker source mounts after the narrow DDL fix.
    for name in ["cluecdc-api", "cluecdc-worker"]:
        stack.fault(name, "restart")
        stack.wait_service(name)
    evidence = Evidence(
        args.fixture.parent / "cross-provider",
        stack.project,
        [
            v
            for k, v in stack.defaults.items()
            if any(x in k for x in ["PASSWORD", "SECRET", "TOKEN", "KEY"])
        ],
    )
    scenario = Scenario(stack, evidence, "postgresql", "MYSQL")
    api = scenario.api
    scenario.pipeline = next(
        p for p in api.request("/pipelines") if p["name"] == scenario.table
    )
    scenario.destination_connection = next(
        c for c in api.request("/connections") if c["name"] == scenario.table + "_dest"
    )
    identifier = scenario.destination_connection["id"]
    scenario.delivery = api.create(
        f"/destinations/{identifier}/deploy",
        {
            "pipeline_id": scenario.pipeline["id"],
            "write_mode": "upsert",
            "mappings": [
                {
                    "topic": scenario.topic,
                    "schema_name": "cluecdc_destination",
                    "table_name": scenario.table,
                }
            ],
            "auto_create": True,
            "auto_evolve": False,
        },
    )
    scenario.record(
        "snapshot",
        "PostgreSQL snapshot applied to MySQL with mapped DDL",
        scenario.snapshot,
    )
    scenario.record(
        "crud",
        "Cross-provider insert/update/delete and exact destination state",
        scenario.crud,
    )
    pipeline = api.request(f"/pipelines/{scenario.pipeline['id']}")
    config = json.loads(stack.path.read_text(encoding="utf-8"))
    exporters = json.loads(
        config["services"]["cluecdc-worker"]["environment"]["OBSERVABILITY_EXPORTERS"]
    )
    exporters.update(trusted_exporters(stack, [pipeline]))
    for name in ["cluecdc-api", "cluecdc-worker"]:
        config["services"][name]["environment"]["OBSERVABILITY_EXPORTERS"] = json.dumps(
            exporters
        )
    stack.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    stack.compose("up", "-d", "--wait", "cluecdc-api", "cluecdc-worker", timeout=180)
    observed = wait_value(
        lambda: api.request(f"/pipelines/{pipeline['id']}/observability"),
        lambda o: (
            o["capture_lag"]["status"] == "AVAILABLE"
            and o["deliveries"]
            and all(d["backlog"]["status"] == "AVAILABLE" for d in o["deliveries"])
        ),
        "cross-provider source/sink telemetry",
        timeout=90,
    )
    (evidence.output / "observation.json").write_text(json.dumps(observed, indent=2))
    print("Real PostgreSQL -> MySQL snapshot, CRUD and attributed telemetry passed")


if __name__ == "__main__":
    main()
