"""Exercise retained CDC offsets across connector and service restarts."""

from __future__ import annotations

import argparse
import http.client
import json
import pathlib
import time
import urllib.error
import urllib.request

from cdc_acceptance_support import (
    ROOT,
    AcceptanceError,
    ApiClient,
    Compose,
    fixture_table_mapping,
    load_fixture_environment,
    wait_for,
)
from kafka_records import sample_records


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
    parser.add_argument(
        "--api-health-url", default="http://localhost:8000/health/ready"
    )
    parser.add_argument("--connect-url", default="http://localhost:8083")
    parser.add_argument("--compose-project", default="cluecdc")
    parser.add_argument(
        "--artifact", type=pathlib.Path, default=ROOT / "artifacts/cdc-integrity.json"
    )
    parser.add_argument("--env-file", action="append", type=pathlib.Path)
    return parser.parse_args()


def http_json(url: str) -> object:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read())
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
        json.JSONDecodeError,
    ) as error:
        raise AcceptanceError(f"Runtime endpoint unavailable: {url}") from error


def connector_snapshot(base_url: str, names: set[str]) -> dict[str, dict]:
    listed = http_json(base_url.rstrip("/") + "/connectors")
    if not isinstance(listed, list) or not names <= set(listed):
        raise AcceptanceError("Expected connectors are missing after restart")
    result = {}
    for name in names:
        config = http_json(base_url.rstrip("/") + f"/connectors/{name}/config")
        if not isinstance(config, dict):
            raise AcceptanceError("Kafka Connect returned an invalid connector config")
        result[name] = config
    return result


def all_running(api: ApiClient, pipeline_id: str, delivery_id: str) -> bool:
    pipeline = api.request(f"/pipelines/{pipeline_id}/status")
    delivery = api.request(f"/deliveries/{delivery_id}/status")
    return (
        pipeline.get("connector", {}).get("state") == "RUNNING"
        and bool(pipeline.get("tasks"))
        and all(task.get("state") == "RUNNING" for task in pipeline["tasks"])
        and delivery.get("actual_state") == "RUNNING"
        and bool(delivery.get("tasks"))
        and all(task.get("state") == "RUNNING" for task in delivery["tasks"])
    )


def wait_running(
    api: ApiClient, pipeline_id: str, delivery_id: str, label: str
) -> None:
    wait_for(
        lambda: all_running(api, pipeline_id, delivery_id),
        bool,
        label,
        240,
    )


def main() -> None:
    args = arguments()
    env_files = args.env_file or [ROOT / ".env"]
    environment = load_fixture_environment(env_files)
    if not args.artifact.is_file():
        raise AcceptanceError(
            "CDC integrity artifact is missing; run scripts/cdc-integrity.py first"
        )
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    run = artifact["run_id"]
    pipeline_id = artifact["pipeline_ids"]["postgresql"]
    api = ApiClient(
        args.api_url,
        [
            environment.get("POSTGRES_DEMO_SOURCE_PASSWORD", ""),
            environment.get("POSTGRES_DEMO_DESTINATION_PASSWORD", ""),
        ],
    )
    compose = Compose(args.compose_project, env_files)
    pipeline = api.request(f"/pipelines/{pipeline_id}")
    deliveries = api.request("/deliveries")
    delivery = next(
        (
            item
            for item in deliveries
            if item["id"] in artifact["delivery_ids"]
            and item["pipeline_id"] == pipeline_id
            and item["destination"]["provider"] == "POSTGRESQL"
        ),
        None,
    )
    if delivery is None:
        raise AcceptanceError("PostgreSQL recovery delivery was not found")
    delivery_id = delivery["id"]
    destination_id = delivery["destination_id"]
    connector_names = {pipeline["connector"]["name"], delivery["connector"]["name"]}
    original_configs = connector_snapshot(args.connect_url, connector_names)
    original_connector_count = len(
        http_json(args.connect_url.rstrip("/") + "/connectors")
    )
    source_table = f"cdc_i_{run}_accounts"
    _, mapping = fixture_table_mapping(pipeline, delivery, source_table)
    destination_table = mapping["table_name"]
    topic = f"{pipeline['topic_prefix']}.public.{source_table}"
    reads_before = sum(
        event["operation"] == "READ" for event in sample_records(topic)["events"]
    )
    next_id = int(time.time() * 1000) % 1_000_000_000

    def write_and_wait(identifier: int, marker: str) -> None:
        compose.postgres(
            "postgres-demo",
            "cluecdc_source",
            f"SET ROLE source_user; INSERT INTO public.{source_table} "
            f"VALUES ({identifier}, '{marker}', NULL, 1.00000000, now());",
        )
        wait_for(
            lambda: compose.postgres(
                "postgres-demo",
                "cluecdc_destination",
                f"SELECT count(*) FROM public.{destination_table} "
                f"WHERE id={identifier} AND name='{marker}';",
            ),
            lambda count: count == "1",
            f"row {identifier} after restart",
            240,
        )

    api.request(f"/pipelines/{pipeline_id}/restart", "POST")
    wait_running(api, pipeline_id, delivery_id, "source connector restart")
    api.request(f"/pipelines/{pipeline_id}/restart-task?task=0", "POST")
    api.request(
        f"/destinations/{destination_id}/deliveries/{delivery_id}/tasks/0/restart",
        "POST",
    )
    wait_running(api, pipeline_id, delivery_id, "individual task restarts")
    write_and_wait(next_id, f"connector-restart-{run}")
    print("PASS connector and individual task restart", flush=True)

    compose.restart("kafka-connect")
    wait_for(
        lambda: connector_snapshot(args.connect_url, connector_names),
        lambda configs: configs == original_configs,
        "Kafka Connect worker recovery with stable connector configs",
        300,
    )
    wait_running(api, pipeline_id, delivery_id, "connect worker restart")
    write_and_wait(next_id + 1, f"connect-worker-restart-{run}")
    print("PASS Kafka Connect worker restart and durable offsets", flush=True)

    compose.restart("cluecdc-api", "cluecdc-worker")
    wait_for(
        lambda: http_json(args.api_health_url),
        lambda health: (
            isinstance(health, dict) and health.get("status") in {"ok", "ready"}
        ),
        "API and worker readiness after restart",
        240,
    )
    wait_running(api, pipeline_id, delivery_id, "API and worker restart")
    write_and_wait(next_id + 2, f"control-plane-restart-{run}")
    print("PASS API and worker restart without data-plane recreation", flush=True)

    compose.stop("kafka")
    compose.postgres(
        "postgres-demo",
        "cluecdc_source",
        f"SET ROLE source_user; INSERT INTO public.{source_table} "
        f"VALUES ({next_id + 3}, 'kafka-outage-{run}', NULL, 2.00000000, now());",
    )
    compose.start("kafka")
    wait_running(api, pipeline_id, delivery_id, "Kafka outage recovery")
    wait_for(
        lambda: compose.postgres(
            "postgres-demo",
            "cluecdc_destination",
            f"SELECT count(*) FROM public.{destination_table} WHERE id={next_id + 3};",
        ),
        lambda count: count == "1",
        "WAL change retained during Kafka outage",
        300,
    )
    print("PASS temporary Kafka outage recovery", flush=True)

    compose.stop("postgres-demo")
    compose.start("postgres-demo")
    wait_for(
        lambda: compose.postgres("postgres-demo", "cluecdc_source", "SELECT 1;"),
        lambda result: result == "1",
        "source database readiness",
        180,
    )
    wait_running(api, pipeline_id, delivery_id, "source database restart")
    write_and_wait(next_id + 4, f"source-restart-{run}")
    print("PASS temporary source database outage recovery", flush=True)

    current_configs = connector_snapshot(args.connect_url, connector_names)
    current_connector_count = len(
        http_json(args.connect_url.rstrip("/") + "/connectors")
    )
    if current_configs != original_configs:
        raise AcceptanceError(
            "Connector identity or configuration changed across restarts"
        )
    if current_connector_count != original_connector_count:
        raise AcceptanceError("Restart unexpectedly created or removed a connector")
    reads_after = sum(
        event["operation"] == "READ" for event in sample_records(topic)["events"]
    )
    if reads_after != reads_before:
        raise AcceptanceError("Restart triggered an unexpected initial snapshot")
    try:
        api.request(f"/pipelines/{pipeline_id}/reset-offsets", "POST")
    except AcceptanceError as error:
        if "HTTP 404" not in str(error) or "INVALID_OPERATION" not in str(error):
            raise
    else:
        raise AcceptanceError(
            "Offset reset must not be exposed as an implicit operation"
        )

    report = {
        "verified": True,
        "run_id": run,
        "pipeline_id": pipeline_id,
        "delivery_id": delivery_id,
        "connector_names": sorted(connector_names),
        "connector_count": current_connector_count,
        "initial_read_events_before": reads_before,
        "initial_read_events_after": reads_after,
        "checks": [
            "connector_restart",
            "task_restart",
            "connect_worker_restart",
            "api_worker_restart",
            "kafka_outage",
            "source_database_outage",
            "stable_configuration",
            "no_implicit_offset_reset",
        ],
    }
    (ROOT / "artifacts" / "cdc-recovery.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print("CDC restart and offset recovery PASSED", flush=True)


if __name__ == "__main__":
    main()
