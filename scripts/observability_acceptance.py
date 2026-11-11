"""Real read-only observability acceptance on explicitly owned disposable CDC fixtures.

Reuses release reliability infrastructure; never reads .env or attaches existing services.
Build observability-connect.Dockerfile first. --retain keeps test-owned services for browser QA.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from cdc_reliability import Scenario
from cdc_reliability_support import (
    ROOT,
    DisposableStack,
    Evidence,
    fixture_port,
    wait_value,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retain", action="store_true")
    parser.add_argument("--api-image", default="cluecdc-cluecdc-api:latest")
    parser.add_argument(
        "--connect-image", default="cluecdc-observability-connect:local"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/observability/live"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    stack = DisposableStack(
        args.api_image, args.connect_image, "cluecdc-minio:latest", retain=args.retain
    )
    config = json.loads(stack.path.read_text(encoding="utf-8"))
    allowed = {
        "metadata-db",
        "kafka",
        "kafka-connect",
        "cluecdc-api",
        "cluecdc-worker",
        "postgres-demo",
        "mysql-demo",
    }
    config["services"] = {k: v for k, v in config["services"].items() if k in allowed}
    for service in config["services"].values():
        service["depends_on"] = {
            k: v for k, v in service.get("depends_on", {}).items() if k in allowed
        }
    # Use the final checkout atop the repository's already built dependency image.
    for name in ("cluecdc-api", "cluecdc-worker"):
        service = config["services"][name]
        service.setdefault("volumes", []).extend(
            [
                {
                    "type": "bind",
                    "source": str(ROOT / "apps/api/app"),
                    "target": "/app/app",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": str(ROOT / "apps/api/migrations"),
                    "target": "/app/migrations",
                    "read_only": True,
                },
            ]
        )
        service["environment"]["OBSERVABILITY_INTERVAL_SECONDS"] = "30"
        service["environment"]["AUTO_RECOVERY_ENABLED"] = "false"
    config["services"]["kafka-connect"].setdefault("ports", []).append(
        {"target": 9404, "published": fixture_port(), "host_ip": "127.0.0.1"}
    )
    config["services"]["metadata-db"]["ports"] = [
        {"target": 5432, "published": fixture_port(), "host_ip": "127.0.0.1"}
    ]
    stack.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    evidence = Evidence(args.output, stack.project)
    evidence.secrets = [
        v
        for k, v in stack.defaults.items()
        if any(n in k for n in ("PASSWORD", "SECRET", "TOKEN", "KEY"))
    ]
    observations = []
    scenarios = []
    (args.output / "fixture.json").write_text(
        json.dumps(
            {
                "project": stack.project,
                "compose_path": str(stack.path),
                "owner": stack.run_id,
            },
            indent=2,
        )
    )
    try:
        print("Provisioning owned fixture " + stack.project, flush=True)
        stack.start()
        for source, target in [
            ("postgresql", "POSTGRESQL"),
            ("mysql", "MYSQL"),
            ("postgresql", "MYSQL"),
        ]:
            print(f"Real CDC: {source} -> {target}", flush=True)
            scenario = Scenario(stack, evidence, source, target)
            try:
                scenario.record("connection", "Live source/sink setup", scenario.setup)
                scenario.record("snapshot", "Real initial snapshot", scenario.snapshot)
                scenario.record(
                    "crud",
                    "Real insert/update/delete and destination state",
                    scenario.crud,
                )
            except Exception:
                # Preserve a failed mandatory gate; continue independent providers.
                stack.diagnostics(evidence)
                continue
            scenarios.append(scenario)
        assert scenarios, "No source/sink pair completed real CDC"
        first = scenarios[0]
        second = first.api.create(
            f"/destinations/{first.destination_connection['id']}/deploy",
            {
                "name": "observability-independent-delivery",
                "pipeline_id": first.pipeline["id"],
                "write_mode": "upsert",
                "mappings": [
                    {
                        "topic": first.topic,
                        "schema_name": "public",
                        "table_name": first.table + "_copy",
                    }
                ],
                "auto_create": True,
                "auto_evolve": False,
            },
        )
        from observability_fixture import trusted_exporters

        exporters = trusted_exporters(
            stack, [s.api.request(f"/pipelines/{s.pipeline['id']}") for s in scenarios]
        )
        for name in ("cluecdc-api", "cluecdc-worker"):
            config["services"][name]["environment"]["OBSERVABILITY_EXPORTERS"] = (
                json.dumps(exporters)
            )
        stack.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        stack.compose(
            "up", "-d", "--wait", "cluecdc-api", "cluecdc-worker", timeout=180
        )
        # Re-sign in after fixture API restart.
        from cdc_reliability_support import api_for

        api = api_for(stack)
        for scenario in scenarios:
            path = f"/pipelines/{scenario.pipeline['id']}/observability"
            observed = wait_value(
                lambda: api.request(path),
                lambda o: (
                    o["capture_lag"]["status"] == "AVAILABLE"
                    and o["deliveries"]
                    and all(
                        d["backlog"]["status"] == "AVAILABLE" for d in o["deliveries"]
                    )
                ),
                "real JMX and Kafka evidence",
                timeout=150,
            )
            assert all(
                d["destination_progress"]["value"] is None
                for d in observed["deliveries"]
            )
            assert all(
                d["valid_partitions"] == d["required_partitions"] > 0
                for d in observed["deliveries"]
            )
            assert all(
                d["sink_metrics"][key]["status"] == "AVAILABLE"
                for d in observed["deliveries"]
                for key in [
                    "active_count",
                    "read_rate",
                    "send_rate",
                    "offset_commit_completion_rate",
                ]
            )
            observations.append(observed)
        assert len(observations[0]["deliveries"]) == 2
        assert len({d["group_id"] for d in observations[0]["deliveries"]}) == 2
        assert second["id"] in {d["delivery_id"] for d in observations[0]["deliveries"]}
        (args.output / "observations.json").write_text(
            json.dumps(observations, indent=2)
        )
        import httpx

        scrape = httpx.get(stack.url("kafka-connect", 9404) + "/metrics", timeout=10)
        scrape.raise_for_status()
        (args.output / "real-jmx.prom").write_text(scrape.text)
        (args.output / "browser.json").write_text(
            json.dumps(
                {
                    "api_url": stack.url("cluecdc-api", 8000),
                    "pipeline_id": scenarios[0].pipeline["id"],
                    "project": stack.project,
                },
                indent=2,
            )
        )
        print("Real capture, sink and Kafka observations verified", flush=True)
        # Run all database-backed worker concurrency tests against the owned metadata service.
        import os

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
        (args.output / "postgres-tests.txt").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, (
            "PostgreSQL collector tests failed; inspect postgres-tests.txt"
        )
        return 0
    except Exception:
        stack.diagnostics(evidence)
        raise
    finally:
        evidence.write()
        stack.close()


if __name__ == "__main__":
    raise SystemExit(main())
