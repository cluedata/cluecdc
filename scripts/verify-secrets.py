"""Fail without printing secrets if credentials appear in browser APIs or service logs."""

import argparse
import json
import os
import pathlib
import subprocess
import urllib.request

root = pathlib.Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
parser.add_argument("--connect-url", default="http://localhost:8083")
args = parser.parse_args()
env = dict(
    line.split("=", 1)
    for line in (root / ".env").read_text().splitlines()
    if "=" in line
)
secret_values = [
    env[key]
    for key in [
        "SOURCE_PASSWORD",
        "SOURCE_ADMIN_PASSWORD",
        "DESTINATION_PASSWORD",
        "DESTINATION_ADMIN_PASSWORD",
        "METADATA_PASSWORD",
        "SECRET_ENCRYPTION_KEY",
        "CONNECT_SECRET_TOKEN",
    ]
]
if os.getenv("CLUECDC_TOKEN"):
    secret_values.append(os.environ["CLUECDC_TOKEN"])


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
            assert runtime_config["connection.password"].startswith("${cluecdc:"), (
                "Sink did not persist a secret reference"
            )
checked(base + "/operations/errors")
checked(base + "/monitoring/overview")
checked(base + "/audit?limit=500")
checked(base + "/connect/connectors")
logs = subprocess.run(
    [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "compose.test.yaml",
        "logs",
        "--no-color",
        "cluecdc-api",
        "kafka-connect",
        "cluecdc-web",
        "destination-postgres",
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
