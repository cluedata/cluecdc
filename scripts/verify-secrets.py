"""Fail without printing secrets if credentials appear in browser APIs or service logs."""

import argparse
import json
import os
import pathlib
import subprocess
import urllib.request

from cdc_acceptance_support import Compose, load_fixture_environment

root = pathlib.Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
parser.add_argument("--connect-url", default="http://localhost:8083")
parser.add_argument("--compose-project")
parser.add_argument("--env-file", action="append", type=pathlib.Path)
args = parser.parse_args()
env_files = args.env_file or [root / ".env"]
env = load_fixture_environment(env_files)
secret_values = [
    env[key]
    for key in [
        "POSTGRES_DEMO_SOURCE_PASSWORD",
        "POSTGRES_DEMO_DESTINATION_PASSWORD",
        "POSTGRES_DEMO_ADMIN_PASSWORD",
        "MYSQL_DEMO_SOURCE_PASSWORD",
        "MYSQL_DEMO_DESTINATION_PASSWORD",
        "MYSQL_DEMO_ADMIN_PASSWORD",
        "METADATA_PASSWORD",
        "SECRET_ENCRYPTION_KEY",
        "SESSION_SECRET",
        "DEFAULT_ADMIN_PASSWORD",
        "CONNECT_SECRET_TOKEN",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ]
    if key in env
]
if os.getenv("CLUECDC_TOKEN"):
    secret_values.append(os.environ["CLUECDC_TOKEN"])
secret_values.extend(
    [
        os.getenv("MINIO_ACCESS_KEY", env.get("MINIO_ACCESS_KEY", "minioadmin")),
        os.getenv("MINIO_SECRET_KEY", env.get("MINIO_SECRET_KEY", "minioadmin")),
    ]
)


def checked(url):
    headers = {}
    if url.startswith(base) and os.getenv("CLUECDC_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["CLUECDC_TOKEN"]
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=30
    ) as response:
        raw = response.read().decode()
    if any(secret in raw for secret in secret_values):
        raise RuntimeError("Credential appeared in an API response; response withheld")
    return json.loads(raw)


base = args.api_url.rstrip("/")
connect_base = args.connect_url.rstrip("/")
sources = checked(base + "/sources")
for source in sources:
    checked(base + "/sources/" + source["id"])
pipelines = checked(base + "/pipelines")
for pipeline in pipelines:
    detail = checked(base + "/pipelines/" + pipeline["id"])
    if detail["connector"]:
        runtime_config = checked(
            connect_base + "/connectors/" + detail["connector"]["name"] + "/config"
        )
        assert runtime_config["database.password"].startswith("${cluecdc:"), (
            "Connect did not persist a secret reference"
        )
destinations = checked(base + "/destinations")
for destination in destinations:
    path = base + "/destinations/" + destination["id"]
    detail = checked(path)
    checked(path + "/mappings")
    checked(path + "/status")
    checked(base + "/audit?resource_id=" + destination["id"] + "&include_related=true")
    for delivery in detail["deliveries"]:
        connector = delivery.get("connector")
        if connector:
            runtime_config = checked(
                connect_base + "/connectors/" + connector["name"] + "/config"
            )
            fields = (
                ["connection.password"]
                if delivery.get("delivery_type", "DATABASE") == "DATABASE"
                else [
                    "cluecdc.access.key",
                    "cluecdc.secret.key",
                    "cluecdc.session.token",
                ]
                if "cluecdc.session.token" in runtime_config
                else ["aws.access.key.id", "aws.secret.access.key"]
            )
            assert all(
                runtime_config[field].startswith("${cluecdc:") for field in fields
            ), "Sink did not persist secret references"
checked(base + "/connections")
checked(base + "/deliveries")
checked(base + "/operations/errors")
checked(base + "/monitoring/overview")
checked(base + "/audit?limit=500")
checked(base + "/connect/connectors")
logs = subprocess.run(
    [
        *Compose(args.compose_project or "cluecdc", env_files).command,
        "logs",
        "--no-color",
        "cluecdc-api",
        "cluecdc-worker",
        "kafka-connect",
        "cluecdc-web",
        "postgres-demo",
        "mysql-demo",
    ],
    cwd=root,
    capture_output=True,
    text=True,
    check=True,
)
if any(secret in logs.stdout or secret in logs.stderr for secret in secret_values):
    raise RuntimeError("Credential appeared in a service log; logs withheld")
print(
    "Browser API responses, stored Connect config, and service logs passed secret checks."
)
