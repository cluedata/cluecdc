"""Live bounded automatic sink-task recovery acceptance test."""

from __future__ import annotations

import argparse
import json
import pathlib
import time

from cdc_acceptance_support import (
    ROOT,
    AcceptanceError,
    ApiClient,
    Compose,
    load_fixture_environment,
    wait_for,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
    parser.add_argument("--compose-project", default="cluecdc")
    parser.add_argument("--env-file", action="append", type=pathlib.Path)
    parser.add_argument(
        "--effective-key-artifact",
        type=pathlib.Path,
        default=ROOT / "artifacts/cdc-effective-key.json",
    )
    parser.add_argument(
        "--artifact",
        type=pathlib.Path,
        default=ROOT / "artifacts/cdc-auto-recovery.json",
    )
    return parser.parse_args()


def delivery_status(api: ApiClient, identifier: str) -> dict:
    return api.request(f"/deliveries/{identifier}/status")


def failure_observed(status: dict) -> bool:
    tasks = status.get("tasks", [])
    return status.get("actual_state") in {"DEGRADED", "FAILED"} and any(
        task.get("state") == "FAILED" for task in tasks
    )


def running(status: dict) -> bool:
    tasks = status.get("tasks", [])
    return (
        status.get("actual_state") == "RUNNING"
        and bool(tasks)
        and all(task.get("state") == "RUNNING" for task in tasks)
    )


def insert_source(compose: Compose, provider: str, table: str, code: str) -> None:
    if provider == "postgresql":
        compose.postgres(
            "postgres-demo",
            "cluecdc_source",
            f"SET ROLE source_user; INSERT INTO public.{table} VALUES "
            f"('{code}', 'Recovery {code}', NULL, 'active');",
        )
    else:
        compose.mysql(
            "mysql-demo",
            "cluecdc_source",
            f"INSERT INTO {table} VALUES ('{code}', 'Recovery {code}', NULL, 'active');",
        )


def destination_has(
    compose: Compose, provider: str, schema: str, table: str, code: str
) -> bool:
    if provider == "postgresql":
        result = compose.postgres(
            "postgres-demo",
            "cluecdc_destination",
            f"SELECT count(*) FROM {schema}.{table} WHERE customer_code='{code}';",
        )
    else:
        result = compose.mysql(
            "mysql-demo",
            "cluecdc_destination",
            f"SELECT count(*) FROM {table} WHERE customer_code='{code}';",
        )
    return result == "1"


def fixture(api: ApiClient, artifact: dict, source_provider: str) -> dict:
    pipeline = api.request(f"/pipelines/{artifact['pipelines'][source_provider]}")
    delivery_id = artifact["deliveries"][source_provider]
    delivery = next(
        row for row in api.request("/deliveries") if row["id"] == delivery_id
    )
    source_table = next(
        table["table_name"]
        for table in pipeline["tables"]
        if table["table_name"].endswith("customers_unique")
    )
    mapping = next(
        item
        for item in delivery["topic_mapping_json"]
        if item["topic"].endswith("customers_unique")
    )
    return {
        "pipeline": pipeline,
        "delivery": delivery,
        "source_table": source_table,
        "destination_schema": mapping["schema_name"],
        "destination_table": mapping["table_name"],
    }


def outage_cycle(
    *,
    api: ApiClient,
    compose: Compose,
    fixture_value: dict,
    source_provider: str,
    destination_provider: str,
    cycle: int,
    restart_worker: bool = False,
) -> dict:
    service = "postgres-demo" if destination_provider == "postgresql" else "mysql-demo"
    delivery_id = fixture_value["delivery"]["id"]
    code = f"REC-{destination_provider[:2].upper()}-{cycle}-{int(time.time())}"
    started = time.monotonic()
    compose.stop(service)
    insert_source(compose, source_provider, fixture_value["source_table"], code)
    failed = wait_for(
        lambda: delivery_status(api, delivery_id),
        failure_observed,
        f"{destination_provider} sink task failure cycle {cycle}",
        180,
    )
    failed_tasks = [task for task in failed["tasks"] if task.get("state") == "FAILED"]
    if not failed_tasks or not all(
        task.get("retryable") is True for task in failed_tasks
    ):
        raise AcceptanceError(
            "Database outage was not classified as a retryable task failure"
        )
    recovery_before = failed.get("auto_recovery", {})
    if restart_worker:
        compose.restart("cluecdc-worker")
        retained = wait_for(
            lambda: delivery_status(api, delivery_id).get("auto_recovery", {}),
            lambda state: (
                int(state.get("attempt_count", 0))
                >= int(recovery_before.get("attempt_count", 0))
            ),
            "persisted recovery state after worker restart",
            120,
        )
    else:
        retained = recovery_before
    compose.start(service)
    wait_for(
        lambda: destination_has(
            compose,
            destination_provider,
            fixture_value["destination_schema"],
            fixture_value["destination_table"],
            code,
        ),
        bool,
        f"retained row after {destination_provider} outage cycle {cycle}",
        300,
    )
    recovered = wait_for(
        lambda: delivery_status(api, delivery_id),
        running,
        f"automatic task recovery cycle {cycle}",
        300,
    )
    recovery = recovered.get("auto_recovery", {})
    if recovery.get("status") not in {"RECOVERED", "IDLE"}:
        raise AcceptanceError(
            "Task returned to RUNNING without verified recovery state"
        )
    return {
        "destination": destination_provider,
        "cycle": cycle,
        "task_ids": [task["id"] for task in failed_tasks],
        "failure_codes": [task.get("error_code") for task in failed_tasks],
        "attempt_count_before_worker_restart": recovery_before.get("attempt_count", 0),
        "attempt_count_after_worker_restart": retained.get("attempt_count", 0),
        "last_cycle_attempts": recovery.get("last_cycle_attempts", 0),
        "last_successful_recovery_at": recovery.get("last_successful_recovery_at"),
        "duration_seconds": round(time.monotonic() - started, 3),
        "row_verified": True,
    }


def main() -> None:
    args = arguments()
    env_files = args.env_file or [ROOT / ".env"]
    environment = load_fixture_environment(env_files)
    if not args.effective_key_artifact.is_file():
        raise AcceptanceError("Run scripts/cdc-effective-key.py first")
    effective = json.loads(args.effective_key_artifact.read_text(encoding="utf-8"))
    api = ApiClient(
        args.api_url,
        [
            environment.get("POSTGRES_DEMO_SOURCE_PASSWORD", ""),
            environment.get("MYSQL_DEMO_SOURCE_PASSWORD", ""),
            environment.get("POSTGRES_DEMO_DESTINATION_PASSWORD", ""),
            environment.get("MYSQL_DEMO_DESTINATION_PASSWORD", ""),
        ],
    )
    api.authenticate(environment)
    compose = Compose(args.compose_project, env_files)
    postgres_target = fixture(api, effective, "mysql")
    mysql_target = fixture(api, effective, "postgresql")
    results = []
    try:
        for cycle in range(1, 4):
            results.append(
                outage_cycle(
                    api=api,
                    compose=compose,
                    fixture_value=postgres_target,
                    source_provider="mysql",
                    destination_provider="postgresql",
                    cycle=cycle,
                    restart_worker=cycle == 2,
                )
            )
            print(f"PASS PostgreSQL automatic recovery cycle {cycle}", flush=True)
        results.append(
            outage_cycle(
                api=api,
                compose=compose,
                fixture_value=mysql_target,
                source_provider="postgresql",
                destination_provider="mysql",
                cycle=1,
            )
        )
        print("PASS MySQL automatic recovery cycle", flush=True)

        compose.stop("kafka-connect")
        unavailable = wait_for(
            lambda: delivery_status(api, postgres_target["delivery"]["id"]),
            lambda status: status.get("actual_state") == "UNKNOWN",
            "Kafka Connect unavailability observation",
            120,
        )
        if unavailable.get("auto_recovery", {}).get("status") not in {
            "OBSERVATION_UNAVAILABLE",
            "IDLE",
        }:
            raise AcceptanceError(
                "Connect outage was incorrectly treated as successful recovery"
            )
        compose.start("kafka-connect")
        wait_for(
            lambda: delivery_status(api, postgres_target["delivery"]["id"]),
            running,
            "delivery after Kafka Connect outage",
            300,
        )
        print("PASS temporary Kafka Connect unavailability", flush=True)
    finally:
        for service in (
            "postgres-demo",
            "mysql-demo",
            "kafka-connect",
            "cluecdc-worker",
        ):
            try:
                compose.start(service)
            except AcceptanceError:
                pass

    report = {
        "verified": True,
        "effective_key_run_id": effective["run_id"],
        "cycles": results,
        "checks": [
            "postgresql_destination_three_cycles",
            "mysql_destination_outage",
            "worker_restart_with_persisted_state",
            "connect_unavailability",
            "task_running_after_recovery",
            "retained_row_delivered",
        ],
    }
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Automatic sink-task recovery acceptance PASSED", flush=True)


if __name__ == "__main__":
    main()
