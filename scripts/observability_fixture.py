"""Attach exclusively to a fixture created by observability_acceptance.py."""

import json
from pathlib import Path

import httpx
from cdc_reliability_support import DisposableStack, isolated_environment


def attach(path: Path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert spec["project"] == "cluecdc-reliability-" + spec["owner"]
    stack = DisposableStack.__new__(DisposableStack)
    stack.run_id, stack.project = spec["owner"], spec["project"]
    stack.path = Path(spec["compose_path"])
    stack.directory, stack.retain = stack.path.parent, True
    from bootstrap import read_values

    stack.defaults = read_values((stack.directory / ".env").read_text(encoding="utf-8"))
    for key in (
        "METADATA_PASSWORD",
        "POSTGRES_DEMO_ADMIN_PASSWORD",
        "POSTGRES_DEMO_SOURCE_PASSWORD",
        "POSTGRES_DEMO_DESTINATION_PASSWORD",
        "MYSQL_DEMO_ADMIN_PASSWORD",
        "MYSQL_DEMO_SOURCE_PASSWORD",
        "MYSQL_DEMO_DESTINATION_PASSWORD",
        "MINIO_SECRET_KEY",
        "CLICKHOUSE_TEST_PASSWORD",
    ):
        stack.defaults[key] = stack.defaults["CLUECDC_LOCAL_PASSWORD"]
    stack.defaults["AUTH_MODE"] = "session"
    stack.env = isolated_environment(stack.defaults)
    for name in json.loads(stack.path.read_text(encoding="utf-8"))["services"]:
        stack.owned_container(
            name
        )  # Verify owner AND Compose project before any mutation.
    return stack


def trusted_exporters(stack, pipelines):
    container = stack.owned_container("kafka-connect")
    info = json.loads(stack.command(["docker", "inspect", container]))[0]
    addresses = {
        v["IPAddress"] + ":8083" for v in info["NetworkSettings"]["Networks"].values()
    }
    addresses.add("kafka-connect:8083")
    exporters = {}
    import urllib.parse

    for pipeline in pipelines:
        name = pipeline["connector"]["name"]
        response = httpx.get(
            stack.url("kafka-connect", 8083)
            + "/connectors/"
            + urllib.parse.quote(name, safe="")
            + "/status",
            timeout=10,
        )
        response.raise_for_status()
        workers = {t["worker_id"] for t in response.json()["tasks"]}
        assert workers and workers <= addresses, "Unverified Connect worker identity"
        exporters[pipeline["connect_cluster_id"]] = {
            worker: "http://kafka-connect:9404/metrics" for worker in workers
        }
    return exporters
