"""Verify telemetry, PostgreSQL claims and safe faults against an owned retained fixture."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from cdc_reliability_support import ROOT, api_for, wait_value
from observability_fixture import attach, trusted_exporters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--faults", action="store_true")
    parser.add_argument("--fault-services", default="postgres-demo,kafka,kafka-connect")
    parser.add_argument("--exporter-fault", action="store_true")
    parser.add_argument("--idle-seconds", type=int, default=0)
    args = parser.parse_args()
    output = args.fixture.parent
    stack = attach(args.fixture)
    api = api_for(stack)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    identifiers = list(
        dict.fromkeys(
            r["observed_behavior"]["pipeline_id"]
            for r in summary["results"]
            if r["scenario"] == "connection" and r["status"] == "PASS"
        )
    )
    pipelines = [api.request(f"/pipelines/{i}") for i in identifiers]
    deliveries = api.request("/deliveries")
    for pipeline in pipelines:
        pipeline["deliveries"] = [
            d for d in deliveries if d["pipeline_id"] == pipeline["id"]
        ]
    config = json.loads(stack.path.read_text(encoding="utf-8"))
    exporters = trusted_exporters(stack, pipelines)
    for name in ("cluecdc-api", "cluecdc-worker"):
        config["services"][name]["environment"]["OBSERVABILITY_EXPORTERS"] = json.dumps(
            exporters
        )
    stack.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    stack.compose("up", "-d", "--wait", "cluecdc-api", "cluecdc-worker", timeout=180)

    def source_event(pipeline):
        table = pipeline["name"]
        assert (
            table.startswith("cdc_" + stack.run_id + "_")
            and table.replace("_", "").isalnum()
        )
        source = "postgresql" if "_po_" in table else "mysql"
        stack.sql(source, f"UPDATE {table} SET label=label WHERE tenant=7 AND id=1")

    for pipeline in pipelines:
        api.create(f"/pipelines/{pipeline['id']}/restart", {})
        for delivery in pipeline["deliveries"]:
            api.create(f"/deliveries/{delivery['id']}/restart", {})
        source_event(pipeline)
    records = []
    for identifier in identifiers:
        observed = wait_value(
            lambda: api.request(f"/pipelines/{identifier}/observability"),
            lambda o: (
                o["capture_lag"]["status"] == "AVAILABLE"
                and all(
                    d["backlog"]["status"] == "AVAILABLE"
                    and d["sink_metrics"]["active_count"]["status"] == "AVAILABLE"
                    for d in o["deliveries"]
                )
            ),
            "verified live exporter and offsets",
            timeout=100,
        )
        assert observed["deliveries"]
        assert all(
            d["destination_progress"]["value"] is None for d in observed["deliveries"]
        )
        assert len({d["group_id"] for d in observed["deliveries"]}) == len(
            observed["deliveries"]
        )
        records.append(observed)
    (output / "observations.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )
    scrape = httpx.get(stack.url("kafka-connect", 9404) + "/metrics", timeout=10)
    scrape.raise_for_status()
    (output / "real-jmx.prom").write_text(scrape.text, encoding="utf-8")
    (output / "browser.json").write_text(
        json.dumps(
            {
                "api_url": stack.url("cluecdc-api", 8000),
                "pipeline_id": identifiers[0],
                "project": stack.project,
            },
            indent=2,
        )
    )
    port = stack.url("metadata-db", 5432).rsplit(":", 1)[1]
    env = {
        **os.environ,
        "WORKER_TEST_DATABASE_URL": f"postgresql+asyncpg://cluecdc:{stack.defaults['METADATA_PASSWORD']}@127.0.0.1:{port}/cluecdc",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "apps/api/tests/test_worker_leases.py",
            "apps/api/tests/test_observability.py",
            "-q",
            "-rs",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (output / "postgres-tests.txt").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, "PostgreSQL tests failed"
    print(
        "Live JMX, per-delivery groups and PostgreSQL concurrency verified", flush=True
    )
    if args.faults:
        faults = []
        path = f"/pipelines/{identifiers[0]}/observability"
        for service in args.fault_services.split(","):
            assert service in {"postgres-demo", "kafka", "kafka-connect"}
            print("Testing owned outage: " + service, flush=True)
            at = datetime.now(UTC).isoformat()
            stack.fault(service, "stop")
            try:
                observed = wait_value(
                    lambda: api.request(path),
                    lambda o: (
                        o["collected_at"]
                        and o["collected_at"] > at
                        and (
                            o["capture_lag"]["value"] is None
                            if service != "kafka"
                            else all(
                                d["backlog"]["value"] is None for d in o["deliveries"]
                            )
                        )
                    ),
                    "honest unavailable classification",
                    timeout=100,
                )
                assert all(
                    d["destination_progress"]["value"] is None
                    for d in observed["deliveries"]
                )
                faults.append(
                    {"service": service, "status": "PASS", "observation": observed}
                )
            finally:
                stack.fault(service, "start")
                stack.wait_service(service)
            (output / "faults.json").write_text(json.dumps(faults, indent=2))
            # Explicit fixture recovery; no observation performs automatic recovery.
            if service == "kafka":
                stack.fault("kafka-connect", "restart")
                stack.wait_service("kafka-connect")
            api.create(f"/pipelines/{identifiers[0]}/restart", {})
            for delivery in pipelines[0]["deliveries"]:
                wait_value(
                    lambda: api.create(f"/deliveries/{delivery['id']}/restart", {}),
                    bool,
                    "explicit fixture delivery recovery",
                    timeout=120,
                )
            source_event(pipelines[0])
            wait_value(
                lambda: api.request(path),
                lambda o: o["capture_lag"]["status"] == "AVAILABLE",
                "restored capture metrics",
                timeout=120,
            )
            faults[-1]["recovery"] = "PASS"
            (output / "faults.json").write_text(json.dumps(faults, indent=2))
    if args.exporter_fault:
        original = config["services"]["cluecdc-worker"]["environment"][
            "OBSERVABILITY_EXPORTERS"
        ]
        missing = {
            key: {worker: "http://kafka-connect:19404/metrics" for worker in workers}
            for key, workers in exporters.items()
        }
        config["services"]["cluecdc-worker"]["environment"][
            "OBSERVABILITY_EXPORTERS"
        ] = json.dumps(missing)
        at = datetime.now(UTC).isoformat()
        stack.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        try:
            stack.compose("up", "-d", "--wait", "cluecdc-worker", timeout=120)
            observed = wait_value(
                lambda: api.request(f"/pipelines/{identifiers[0]}/observability"),
                lambda o: (
                    o["collected_at"] > at
                    and o["capture_lag"]["value"] is None
                    and all(
                        d["backlog"]["status"] == "AVAILABLE" for d in o["deliveries"]
                    )
                ),
                "exporter missing with usable Kafka metadata",
                timeout=100,
            )
            (output / "exporter-missing.json").write_text(
                json.dumps({"status": "PASS", "observation": observed}, indent=2)
            )
        finally:
            config["services"]["cluecdc-worker"]["environment"][
                "OBSERVABILITY_EXPORTERS"
            ] = original
            stack.path.write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8"
            )
            stack.compose("up", "-d", "--wait", "cluecdc-worker", timeout=120)
    if args.idle_seconds:
        assert args.idle_seconds >= 300
        api.create(
            "/alert-rules",
            {
                "name": "Owned observability idle acceptance",
                "event_types": ["CDC_SINK_PROGRESS_STALLED"],
                "pipeline_filters": [identifiers[0]],
            },
        )
        samples = {}
        started = time.monotonic()
        while time.monotonic() - started < args.idle_seconds:
            observed = api.request(f"/pipelines/{identifiers[0]}/observability")
            assert all(
                d["backlog"]["status"] == "AVAILABLE" and d["backlog"]["value"] == 0
                for d in observed["deliveries"]
            )
            assert not any(
                f["code"] == "SINK_PROGRESS_STALLED" for f in observed["findings"]
            )
            samples[observed["collected_at"]] = observed
            time.sleep(5)
        assert len(samples) >= 3
        alerts = api.request(
            f"/alerts?eventType=CDC_SINK_PROGRESS_STALLED&pipelineId={identifiers[0]}"
        )
        assert alerts["total"] == 0
        (output / "idle.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "duration_seconds": time.monotonic() - started,
                    "samples": list(samples.values()),
                    "active_stall_alerts": alerts["total"],
                },
                indent=2,
            )
        )
        print("Measured idle source passed the five-minute stall window", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
