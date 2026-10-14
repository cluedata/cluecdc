"""Inject control-plane and database failures without resetting CDC state."""

from __future__ import annotations

import argparse
import http.client
import json
import pathlib
import urllib.error
import urllib.request
import uuid

from cdc_acceptance_support import (
    ROOT,
    AcceptanceError,
    ApiClient,
    Compose,
    load_environment,
    wait_for,
)


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
    parser.add_argument(
        "--recovery-artifact",
        type=pathlib.Path,
        default=ROOT / "artifacts/cdc-recovery.json",
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
        raise AcceptanceError("Expected connectors are missing")
    result = {}
    for name in names:
        config = http_json(base_url.rstrip("/") + f"/connectors/{name}/config")
        if not isinstance(config, dict):
            raise AcceptanceError("Kafka Connect returned an invalid connector config")
        result[name] = config
    return result


def desired_actual(
    api: ApiClient, pipeline_id: str, delivery_id: str
) -> tuple[str, str, str, str]:
    pipeline = api.request(f"/pipelines/{pipeline_id}/status")
    delivery = api.request(f"/deliveries/{delivery_id}/status")
    return (
        pipeline["desired_state"],
        pipeline["actual_state"],
        delivery["desired_state"],
        delivery["actual_state"],
    )


def wait_states(
    api: ApiClient,
    pipeline_id: str,
    delivery_id: str,
    expected: tuple[str, str, str, str],
    label: str,
    seconds: int = 240,
) -> None:
    wait_for(
        lambda: desired_actual(api, pipeline_id, delivery_id),
        lambda state: state == expected,
        label,
        seconds,
    )


def expect_unavailable(operation, code: str) -> None:
    try:
        operation()
    except AcceptanceError as error:
        if code not in str(error):
            raise
    else:
        raise AcceptanceError(f"Expected {code} while dependency was unavailable")


def api_is_ready(value: object) -> bool:
    return isinstance(value, dict) and value.get("status") in {"ok", "ready"}


def main() -> None:
    args = arguments()
    env_files = args.env_file or [ROOT / ".env"]
    environment = load_environment(env_files)
    if not args.artifact.is_file() or not args.recovery_artifact.is_file():
        raise AcceptanceError(
            "Run cdc-integrity.py and cdc-recovery.py before this test"
        )
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    recovery = json.loads(args.recovery_artifact.read_text(encoding="utf-8"))
    required_prior = {
        "connect_worker_restart",
        "api_worker_restart",
        "kafka_outage",
        "source_database_outage",
        "stable_configuration",
    }
    if not recovery.get("verified") or not required_prior <= set(
        recovery.get("checks", [])
    ):
        raise AcceptanceError(
            "CDC recovery artifact does not contain the required fault matrix"
        )

    api = ApiClient(
        args.api_url,
        [
            environment.get("SOURCE_PASSWORD", ""),
            environment.get("DESTINATION_PASSWORD", ""),
        ],
    )
    compose = Compose(args.compose_project, env_files)
    pipeline_id = artifact["pipeline_ids"]["postgresql"]
    delivery_id = next(
        identifier
        for identifier in artifact["delivery_ids"]
        if api.request(f"/deliveries/{identifier}")["pipeline_id"] == pipeline_id
        and api.request(f"/deliveries/{identifier}")["destination"]["provider"]
        == "POSTGRESQL"
    )
    pipeline = api.request(f"/pipelines/{pipeline_id}")
    delivery = api.request(f"/deliveries/{delivery_id}")
    source_id = pipeline["source_id"]
    destination_id = delivery["destination_id"]
    source_connector = pipeline["connector"]["name"]
    sink_connector = delivery["connector"]["name"]
    connector_names = {source_connector, sink_connector}
    original_configs = connector_snapshot(args.connect_url, connector_names)
    original_count = len(http_json(args.connect_url.rstrip("/") + "/connectors"))
    source_table = pipeline["tables"][0]["table_name"]
    mapping = delivery["topic_mapping_json"][0]
    destination_table = mapping["table_name"]
    destination_schema = mapping["schema_name"]
    paused = False

    try:
        baseline = desired_actual(api, pipeline_id, delivery_id)
        if baseline[0] == "PAUSED":
            api.request(f"/pipelines/{pipeline_id}/resume", "POST")
        elif baseline[0] != "RUNNING":
            raise AcceptanceError(
                f"Pipeline fixture must be RUNNING or PAUSED, got {baseline[0]}"
            )
        if baseline[2] == "PAUSED":
            api.request(
                f"/destinations/{destination_id}/resume?delivery_id={delivery_id}",
                "POST",
            )
        elif baseline[2] != "RUNNING":
            raise AcceptanceError(
                f"Delivery fixture must be RUNNING or PAUSED, got {baseline[2]}"
            )
        wait_states(
            api,
            pipeline_id,
            delivery_id,
            ("RUNNING", "RUNNING", "RUNNING", "RUNNING"),
            "baseline running state",
        )
        api.request(f"/pipelines/{pipeline_id}/pause", "POST")
        api.request(
            f"/destinations/{destination_id}/pause?delivery_id={delivery_id}", "POST"
        )
        paused = True
        wait_states(
            api,
            pipeline_id,
            delivery_id,
            ("PAUSED", "PAUSED", "PAUSED", "PAUSED"),
            "manual pause",
        )

        compose.stop("kafka-connect")
        wait_states(
            api,
            pipeline_id,
            delivery_id,
            ("PAUSED", "UNKNOWN", "PAUSED", "UNKNOWN"),
            "Connect REST outage observation",
            120,
        )
        if any(
            table["cdc_status"] != "PAUSED"
            for table in api.request(f"/pipelines/{pipeline_id}")["tables"]
        ):
            raise AcceptanceError("Connect outage erased a table's manual pause state")
        compose.start("kafka-connect")
        wait_for(
            lambda: http_json(args.connect_url.rstrip("/") + "/connectors"),
            lambda value: isinstance(value, list),
            "Kafka Connect restart",
            240,
        )
        wait_states(
            api,
            pipeline_id,
            delivery_id,
            ("PAUSED", "PAUSED", "PAUSED", "PAUSED"),
            "paused connectors after Connect restart",
        )

        compose.restart("cluecdc-api", "cluecdc-worker")
        wait_for(
            lambda: http_json(args.api_health_url),
            api_is_ready,
            "API restart",
            180,
        )
        wait_states(
            api,
            pipeline_id,
            delivery_id,
            ("PAUSED", "PAUSED", "PAUSED", "PAUSED"),
            "pause preserved across API and worker restart",
        )
        print(
            "PASS Connect/API/worker failures preserve manual pause intent", flush=True
        )

        api.request(f"/pipelines/{pipeline_id}/resume", "POST")
        api.request(
            f"/destinations/{destination_id}/resume?delivery_id={delivery_id}", "POST"
        )
        paused = False
        wait_states(
            api,
            pipeline_id,
            delivery_id,
            ("RUNNING", "RUNNING", "RUNNING", "RUNNING"),
            "manual resume",
        )

        compose.stop("cdc-source-postgres")
        expect_unavailable(
            lambda: api.request(f"/sources/{source_id}/test", "POST"),
            "SOURCE_UNAVAILABLE",
        )
        compose.start("cdc-source-postgres")
        wait_for(
            lambda: compose.postgres("cdc-source-postgres", "commerce", "SELECT 1;"),
            lambda value: value == "1",
            "source database restart",
            180,
        )
        wait_for(
            lambda: api.request(f"/sources/{source_id}/test", "POST"),
            lambda value: value.get("status") in {"passed", "warning"},
            "source health recovery",
            180,
        )
        print("PASS source outage is visible and health recovers", flush=True)

        next_id = int(
            compose.postgres(
                "cdc-source-postgres",
                "commerce",
                f"SELECT COALESCE(max(id),0)+100 FROM public.{source_table};",
            )
        )
        run = uuid.uuid4().hex[:8]
        compose.stop("destination-postgres")
        expect_unavailable(
            lambda: api.request(f"/destinations/{destination_id}/test", "POST"),
            "DESTINATION_UNAVAILABLE",
        )
        compose.postgres(
            "cdc-source-postgres",
            "commerce",
            f"SET ROLE cdc_user; INSERT INTO public.{source_table} "
            f"VALUES ({next_id}, 'destination-outage-{run}', NULL, 4.00000000, now());",
        )
        wait_for(
            lambda: api.request(f"/deliveries/{delivery_id}/status"),
            lambda value: value["actual_state"] in {"FAILED", "DEGRADED"},
            "destination failure observation",
            180,
        )
        compose.start("destination-postgres")
        wait_for(
            lambda: compose.postgres("destination-postgres", "analytics", "SELECT 1;"),
            lambda value: value == "1",
            "destination database restart",
            180,
        )
        api.request(
            f"/destinations/{destination_id}/restart?delivery_id={delivery_id}", "POST"
        )
        wait_states(
            api,
            pipeline_id,
            delivery_id,
            ("RUNNING", "RUNNING", "RUNNING", "RUNNING"),
            "destination task recovery",
        )
        wait_for(
            lambda: compose.postgres(
                "destination-postgres",
                "analytics",
                f"SELECT count(*) FROM {destination_schema}.{destination_table} WHERE id={next_id};",
            ),
            lambda value: value == "1",
            "destination catch-up after outage",
            240,
        )
        print(
            "PASS destination interruption is visible and catches up safely", flush=True
        )

        current_configs = connector_snapshot(args.connect_url, connector_names)
        current_count = len(http_json(args.connect_url.rstrip("/") + "/connectors"))
        if current_configs != original_configs or current_count != original_count:
            raise AcceptanceError(
                "Failure recovery recreated or changed connector infrastructure"
            )
        final = desired_actual(api, pipeline_id, delivery_id)
        if final != ("RUNNING", "RUNNING", "RUNNING", "RUNNING"):
            raise AcceptanceError(
                "Desired and observed states did not converge after recovery"
            )
        print(
            "PASS connector identity, configuration, and metadata remained stable",
            flush=True,
        )
    finally:
        for service in [
            "kafka",
            "cdc-source-postgres",
            "destination-postgres",
            "kafka-connect",
            "cluecdc-api",
            "cluecdc-worker",
        ]:
            try:
                compose.start(service)
            except AcceptanceError:
                pass
        if paused:
            try:
                wait_for(
                    lambda: http_json(args.api_health_url),
                    api_is_ready,
                    "API cleanup readiness",
                    180,
                )
                api.request(f"/pipelines/{pipeline_id}/resume", "POST")
                api.request(
                    f"/destinations/{destination_id}/resume?delivery_id={delivery_id}",
                    "POST",
                )
            except AcceptanceError:
                pass

    report = {
        "verified": True,
        "pipeline_id": pipeline_id,
        "delivery_id": delivery_id,
        "checks": sorted(
            required_prior
            | {
                "connect_rest_unavailable",
                "manual_pause_preserved",
                "destination_database_outage",
                "temporary_network_interruption",
                "observed_state_convergence",
            }
        ),
    }
    output = ROOT / "artifacts/cdc-failure-recovery.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("CDC failure recovery PASSED", flush=True)


if __name__ == "__main__":
    main()
