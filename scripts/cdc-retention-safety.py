"""Verify live PostgreSQL WAL-slot and MySQL binlog retention diagnostics."""

from __future__ import annotations

import argparse
import json
import pathlib
import uuid

from cdc_acceptance_support import (
    ROOT,
    AcceptanceError,
    ApiClient,
    Compose,
    load_fixture_environment,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
    parser.add_argument("--compose-project", default="cluecdc")
    parser.add_argument(
        "--artifact", type=pathlib.Path, default=ROOT / "artifacts/cdc-integrity.json"
    )
    parser.add_argument("--env-file", action="append", type=pathlib.Path)
    return parser.parse_args()


def checks(result: dict) -> dict[str, dict]:
    return {item["key"]: item for item in result.get("checks", [])}


def main() -> None:
    args = arguments()
    env_files = args.env_file or [ROOT / ".env"]
    environment = load_fixture_environment(env_files)
    if not args.artifact.is_file():
        raise AcceptanceError(
            "CDC integrity artifact is missing; run cdc-integrity.py first"
        )
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    api = ApiClient(
        args.api_url,
        [
            environment.get("POSTGRES_DEMO_SOURCE_PASSWORD", ""),
            environment.get("MYSQL_DEMO_SOURCE_PASSWORD", ""),
        ],
    )
    compose = Compose(args.compose_project, env_files)
    postgres_pipeline = api.request(
        f"/pipelines/{artifact['pipeline_ids']['postgresql']}"
    )
    postgres_source_id = postgres_pipeline["source_id"]

    baseline = api.request(f"/sources/{postgres_source_id}/cdc-readiness")
    if baseline.get("monitoring_limited") or "replication_slot_health" not in checks(
        baseline
    ):
        raise AcceptanceError("PostgreSQL logical slot monitoring is not available")
    print(
        "PASS PostgreSQL logical slot state and retained WAL are measurable", flush=True
    )

    slot = "cluecdc_retention_probe_" + uuid.uuid4().hex[:8]
    try:
        compose.postgres(
            "postgres-demo",
            "cluecdc_source",
            f"SELECT pg_create_logical_replication_slot('{slot}', 'pgoutput');",
        )
        observed = api.request(f"/sources/{postgres_source_id}/cdc-readiness")
        matching = [
            item for item in observed["replication_slots"] if item["slot_name"] == slot
        ]
        if (
            len(matching) != 1
            or matching[0]["active"]
            or not observed["retention_risk"]
        ):
            raise AcceptanceError(
                "Inactive PostgreSQL probe slot was not reported as retention risk"
            )
        still_present = compose.postgres(
            "postgres-demo",
            "cluecdc_source",
            f"SELECT count(*) FROM pg_replication_slots WHERE slot_name='{slot}';",
        )
        if still_present != "1":
            raise AcceptanceError(
                "Readiness mutated the recoverable PostgreSQL probe slot"
            )
        print("PASS inactive slot warns without automatic cleanup", flush=True)
    finally:
        compose.postgres(
            "postgres-demo",
            "cluecdc_source",
            "SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots "
            f"WHERE slot_name='{slot}' AND NOT active;",
        )

    run = uuid.uuid4().hex[:8]
    mysql_password = environment.get("MYSQL_DEMO_SOURCE_PASSWORD", "")
    if not mysql_password:
        raise AcceptanceError("MYSQL_DEMO_SOURCE_PASSWORD is required")
    mysql_payload = {
        "name": f"Retention probe {run}",
        "type": "mysql",
        "environment": "TEST",
        "host": "mysql-demo",
        "port": 3306,
        "database_name": "cluecdc_source",
        "username": "source_user",
        "password": mysql_password,
        "ssl_enabled": False,
        "provider_options": {
            "connection_timeout_seconds": 10,
            "max_connector_outage_seconds": 700000,
        },
    }
    mysql_source = api.create("/sources", mysql_payload)
    try:
        mysql = api.request(f"/sources/{mysql_source['id']}/test", "POST")
        mysql_checks = checks(mysql)
        if mysql.get("binlog_retention_seconds") != 604800:
            raise AcceptanceError(
                "MySQL configured binlog retention was not reported exactly"
            )
        if mysql.get("monitoring_limited") or not mysql.get("binlog_position"):
            raise AcceptanceError("MySQL binlog position/inventory is not visible")
        if not mysql.get("retention_risk"):
            raise AcceptanceError(
                "MySQL outage objective exceeding retention was not detected"
            )
        if mysql_checks["binlog_retention_window"]["status"] != "warning":
            raise AcceptanceError(
                "MySQL retention risk did not produce a readiness warning"
            )
        alerts = api.request("/alerts?page=1&pageSize=100&status=active")
        if not any(
            item["event_type"] == "CDC_SOURCE_LOG_RETENTION_RISK"
            and item["source_id"] == mysql_source["id"]
            for item in alerts["items"]
        ):
            raise AcceptanceError(
                "MySQL retention risk did not create a critical alert"
            )
        print(
            "PASS MySQL retention horizon, position, inventory, and alert", flush=True
        )

        recovered = {
            **mysql_payload,
            "password": None,
            "provider_options": {
                "connection_timeout_seconds": 10,
                "max_connector_outage_seconds": 300,
            },
        }
        api.request(f"/sources/{mysql_source['id']}", "PUT", recovered)
        healthy = api.request(f"/sources/{mysql_source['id']}/test", "POST")
        if healthy.get("retention_risk"):
            raise AcceptanceError(
                "Safe MySQL outage objective still reports retention risk"
            )
        print("PASS healthy retention observation resolves the risk", flush=True)
    finally:
        api.request(f"/sources/{mysql_source['id']}", "DELETE")

    report = {
        "verified": True,
        "postgres_source_id": postgres_source_id,
        "checks": [
            "postgres_slot_inventory",
            "postgres_inactive_slot_no_mutation",
            "mysql_retention_window",
            "mysql_binlog_position_inventory",
            "retention_alert_lifecycle",
        ],
    }
    output = ROOT / "artifacts/cdc-retention-safety.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("CDC retention safety PASSED", flush=True)


if __name__ == "__main__":
    main()
