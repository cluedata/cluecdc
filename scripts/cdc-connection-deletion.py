"""Live acceptance test for connection deletion and dependency races."""

from __future__ import annotations

import argparse
import json
import pathlib
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from cdc_acceptance_support import (
    ROOT,
    AcceptanceError,
    ApiClient,
    load_fixture_environment,
    wait_for,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
    parser.add_argument("--env-file", action="append", type=pathlib.Path)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument(
        "--effective-key-artifact",
        type=pathlib.Path,
        default=ROOT / "artifacts/cdc-effective-key.json",
    )
    parser.add_argument(
        "--artifact",
        type=pathlib.Path,
        default=ROOT / "artifacts/cdc-connection-deletion.json",
    )
    return parser.parse_args()


def raw_request(
    api: ApiClient,
    path: str,
    method: str,
    data: object | None,
    barrier: threading.Barrier,
    delay_seconds: float = 0,
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        api.base_url + path,
        method=method,
        headers={"Content-Type": "application/json"},
        data=json.dumps(data).encode() if data is not None else None,
    )
    api.cookie_jar.add_cookie_header(request)
    barrier.wait(timeout=10)
    if delay_seconds:
        time.sleep(delay_seconds)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode(errors="replace"))
        except json.JSONDecodeError:
            payload = {}
        return error.code, payload


def run_race(
    api: ApiClient,
    environment: dict[str, str],
    fixture: dict[str, Any],
    iteration: int,
) -> dict[str, Any]:
    suffix = f"{uuid.uuid4().hex[:8]}_{iteration}"
    source = api.create(
        "/sources",
        {
            "name": f"Connection deletion race {suffix}",
            "type": "postgresql",
            "host": "postgres-demo",
            "port": 5432,
            "database_name": "cluecdc_source",
            "username": "source_user",
            "password": environment["POSTGRES_DEMO_SOURCE_PASSWORD"],
        },
    )
    source_id = source["id"]
    discovery = api.request(f"/sources/{source_id}/discover", "POST")
    completed = wait_for(
        lambda: api.request(f"/jobs/{discovery['id']}"),
        lambda job: job["status"] in {"COMPLETED", "FAILED"},
        "connection deletion race source discovery",
    )
    if completed["status"] != "COMPLETED":
        raise AcceptanceError("Connection deletion race source discovery failed")
    table = fixture["tables"][0]
    pipeline_payload = {
        "name": f"Connection deletion race {suffix}",
        "source_id": source_id,
        "kafka_cluster_id": fixture["kafka_cluster"]["id"],
        "connect_cluster_id": fixture["connect_cluster"]["id"],
        "topic_prefix": f"delete_race_{suffix}",
        "snapshot_mode": "no_data",
        "tables": [
            {
                "schema_name": table["schema_name"],
                "table_name": table["table_name"],
            }
        ],
    }
    barrier = threading.Barrier(2)
    results: dict[str, tuple[int, dict[str, Any]]] = {}

    def create_pipeline() -> None:
        results["create"] = raw_request(
            api, "/pipelines", "POST", pipeline_payload, barrier
        )

    def delete_connection() -> None:
        results["delete"] = raw_request(
            api,
            f"/connections/{source_id}",
            "DELETE",
            None,
            barrier,
            delay_seconds=3 if iteration % 2 == 0 else 0,
        )

    threads = [
        threading.Thread(target=create_pipeline, daemon=True),
        threading.Thread(target=delete_connection, daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=130)
    if any(thread.is_alive() for thread in threads) or set(results) != {
        "create",
        "delete",
    }:
        raise AcceptanceError(
            "Concurrent connection deletion requests did not complete"
        )

    create_status, create_body = results["create"]
    delete_status, delete_body = results["delete"]
    outcome: str
    if create_status == 201 and delete_status == 409:
        outcome = "dependency_won"
        error = delete_body.get("error", {})
        if error.get("code") != "CONNECTION_IN_USE" or not error.get("details", {}).get(
            "used_by"
        ):
            raise AcceptanceError(
                "Blocked deletion did not return structured dependencies"
            )
    elif delete_status == 200 and create_status >= 400:
        outcome = "deletion_won"
    else:
        create_error = create_body.get("error", {})
        raise AcceptanceError(
            "Unsafe race result: "
            f"pipeline create HTTP {create_status} "
            f"({create_error.get('code', 'UNKNOWN')}: "
            f"{create_error.get('message', 'Request failed')}), "
            f"connection delete HTTP {delete_status}"
        )

    pipelines = api.request("/pipelines")
    referencing = [item for item in pipelines if item["source_id"] == source_id]
    if outcome == "deletion_won" and referencing:
        raise AcceptanceError("A pipeline references a connection deleted by the race")
    if outcome == "dependency_won" and len(referencing) != 1:
        raise AcceptanceError(
            "The winning pipeline dependency was not persisted exactly once"
        )

    for pipeline in referencing:
        api.request(f"/pipelines/{pipeline['id']}", "DELETE")
    if outcome == "dependency_won":
        api.request(f"/connections/{source_id}", "DELETE")
    return {
        "iteration": iteration,
        "outcome": outcome,
        "create_status": create_status,
        "create_error_code": create_body.get("error", {}).get("code"),
        "delete_status": delete_status,
        "dangling_references": 0,
    }


def main() -> None:
    args = arguments()
    if args.iterations < 1:
        raise AcceptanceError("--iterations must be positive")
    environment = load_fixture_environment(args.env_file or [ROOT / ".env"])
    password = environment.get("POSTGRES_DEMO_SOURCE_PASSWORD")
    if not password:
        raise AcceptanceError("POSTGRES_DEMO_SOURCE_PASSWORD is required")
    if not args.effective_key_artifact.is_file():
        raise AcceptanceError(
            "Run scripts/cdc-effective-key.py before this acceptance test"
        )
    evidence = json.loads(args.effective_key_artifact.read_text(encoding="utf-8"))
    if not evidence.get("verified"):
        raise AcceptanceError("The effective-key fixture is not verified")

    api = ApiClient(args.api_url, [password])
    api.authenticate(environment)
    fixture = api.request(f"/pipelines/{evidence['pipelines']['postgresql']}")
    started = time.monotonic()
    races = [
        run_race(api, environment, fixture, index + 1)
        for index in range(args.iterations)
    ]
    artifact = {
        "verified": True,
        "effective_key_run_id": evidence["run_id"],
        "iterations": args.iterations,
        "races": races,
        "checks": [
            "concurrent_pipeline_create_and_connection_delete",
            "structured_dependency_conflict",
            "no_dangling_pipeline_reference",
            "post_race_cleanup",
        ],
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"Connection deletion race acceptance PASSED ({args.iterations} iterations)")


if __name__ == "__main__":
    main()
