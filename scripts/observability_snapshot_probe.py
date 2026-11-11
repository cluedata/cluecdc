"""Run the existing real interrupted-snapshot reliability scenario on an owned fixture."""

import argparse
import json
from pathlib import Path

from cdc_reliability import Scenario
from cdc_reliability_support import Evidence
from cdc_reliability_support import wait_value
from observability_fixture import attach


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--incremental-only", action="store_true")
    parser.add_argument("--name-suffix", default="snapshot_probe")
    args = parser.parse_args()
    stack = attach(args.fixture)
    evidence = Evidence(
        args.fixture.parent
        / "snapshots"
        / ("incremental" if args.incremental_only else "initial"),
        stack.project,
        [
            v
            for k, v in stack.defaults.items()
            if any(x in k for x in ["PASSWORD", "SECRET", "TOKEN", "KEY"])
        ],
    )
    scenario = Scenario(stack, evidence, "postgresql", "POSTGRESQL")
    assert args.name_suffix.replace("_", "").isalnum()
    scenario.table += "_" + args.name_suffix
    scenario.topic = scenario.table + ".public." + scenario.table
    if args.incremental_only:
        scenario.pipeline = next(
            p for p in scenario.api.request("/pipelines") if p["name"] == scenario.table
        )
        pipeline = scenario.api.request(f"/pipelines/{scenario.pipeline['id']}")
        table_id = pipeline["tables"][0]["id"]
        probe = """import json,sys
from confluent_kafka import Consumer,TopicPartition
t=sys.stdin.read()
c=Consumer({"bootstrap.servers":"kafka:29092","group.id":"owned-snapshot-tail","enable.auto.commit":False,"enable.auto.offset.store":False})
try:
 lo,hi=c.get_watermark_offsets(TopicPartition(t,0),timeout=5)
 c.assign([TopicPartition(t,0,max(lo,hi-1))])
 m=c.poll(3)
 v=json.loads(m.value()) if m and m.value() else {}
 v=v.get("payload",v)
 print(json.dumps({"end":hi,"snapshot":v.get("source",{}).get("snapshot"),"op":v.get("op")}))
finally:c.close()
"""
        before = json.loads(
            stack.exec("cluecdc-api", ["python", "-c", probe], scenario.topic)
        )
        operation = scenario.api.create(f"/pipeline-tables/{table_id}/resync", {})
        progress = wait_value(
            lambda: json.loads(
                stack.exec("cluecdc-api", ["python", "-c", probe], scenario.topic)
            ),
            lambda p: (
                p["end"] > before["end"]
                and p["snapshot"] == "incremental"
                and p["op"] == "r"
            ),
            "real incremental snapshot records",
            timeout=90,
        )
        scenario.api.create(f"/pipelines/{scenario.pipeline['id']}/restart", {})
        completed = wait_value(
            lambda: scenario.api.request(f"/operations/{operation['id']}"),
            lambda o: o["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"},
            "interrupted incremental operation completion",
            timeout=300,
        )
        assert completed["status"] == "SUCCEEDED", completed.get("error_code")
        query = f"SELECT count(*),md5(string_agg(tenant::text||':'||id::text||':'||label,',' ORDER BY tenant,id)) FROM {scenario.table}"
        expected = stack.sql("postgresql", query)
        wait_value(
            lambda: stack.sql("postgresql", query, "cluecdc_destination"),
            lambda value: value == expected,
            "incremental exact destination state",
            timeout=90,
        )
        evidence.add(
            "interrupted-incremental-snapshot",
            "postgresql",
            "POSTGRESQL",
            "CURRENT_STATE",
            "interrupted-incremental-snapshot",
            "Real incremental records, explicit restart, durable completion and exact destination state",
            observed={
                "before": before,
                "progress": progress,
                "operation": completed["status"],
                "digest": expected,
            },
        )
        print("Interrupted incremental snapshot acceptance passed")
        return
    scenario.record(
        "interrupted-initial-snapshot",
        "Real in-progress initial snapshot interruption "
        "and eventual exact destination count/key digest",
        scenario.interrupt_snapshot,
    )
    observed = scenario.api.request(
        f"/pipelines/{scenario.pipeline['id']}/observability"
    )
    (evidence.output / "observation.json").write_text(json.dumps(observed, indent=2))
    print("Existing interrupted initial snapshot acceptance passed")


if __name__ == "__main__":
    main()
