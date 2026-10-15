"""Shared primitives for isolated live CDC acceptance tests.

This module intentionally uses only the Python standard library. It is imported by
the release acceptance scripts and unit-tested by the API test suite.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar

ROOT = pathlib.Path(__file__).resolve().parent.parent
T = TypeVar("T")


class AcceptanceError(RuntimeError):
    """An actionable acceptance failure that is safe to print."""


def load_environment(paths: Sequence[pathlib.Path]) -> dict[str, str]:
    """Load simple dotenv files in order, with process variables taking priority."""

    values: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            raise AcceptanceError(f"Environment file does not exist: {path}")
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    values.update({key: value for key, value in os.environ.items() if key in values})
    return values


class ApiClient:
    def __init__(self, base_url: str, sensitive_values: Iterable[str] = ()) -> None:
        self.base_url = base_url.rstrip("/")
        self.sensitive_values = tuple(value for value in sensitive_values if value)

    def request(
        self, path: str, method: str = "GET", data: object | None = None
    ) -> Any:
        headers = {"Content-Type": "application/json"}
        if token := os.environ.get("CLUECDC_TOKEN"):
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(
            self.base_url + path,
            method=method,
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode()
        except urllib.error.HTTPError as error:
            raw_error = error.read().decode(errors="replace")
            try:
                payload = json.loads(raw_error).get("error", {})
                code = payload.get("code", "HTTP_ERROR")
                message = payload.get("message", "Request failed")
                details = json.dumps(payload.get("details", {}), ensure_ascii=False)
            except json.JSONDecodeError:
                code, message, details = "HTTP_ERROR", "Request failed", "{}"
            diagnostic = f"{code} {message}; details={details}"
            for secret in self.sensitive_values:
                diagnostic = diagnostic.replace(secret, "[REDACTED]")
            raise AcceptanceError(
                f"{method} {path} failed with HTTP {error.code}: {diagnostic[:2000]}"
            ) from None
        if any(secret in raw for secret in self.sensitive_values):
            raise AcceptanceError(f"{method} {path} exposed a configured secret")
        return json.loads(raw) if raw else None

    def create(self, path: str, data: Mapping[str, object]) -> Any:
        return self.request(path, "POST", data)


class Compose:
    def __init__(self, project: str, env_files: Sequence[pathlib.Path]) -> None:
        self.command = ["docker", "compose"]
        for path in env_files:
            self.command.extend(["--env-file", str(path)])
        self.command.extend(
            [
                "-p",
                project,
                "-f",
                "compose.yaml",
                "-f",
                "compose.test.yaml",
            ]
        )

    def run(self, *arguments: str, timeout: int = 180) -> str:
        result = subprocess.run(
            [*self.command, *arguments],
            encoding="utf-8",
            cwd=ROOT,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode:
            raise AcceptanceError(
                f"Compose {' '.join(arguments)} failed with exit code "
                f"{result.returncode}; service output withheld"
            )
        return result.stdout.strip()

    def restart(self, *services: str) -> None:
        self.run("restart", *services)

    def stop(self, service: str) -> None:
        self.run("stop", service)

    def start(self, service: str) -> None:
        self.run("start", service)

    def exec(self, service: str, command: Sequence[str], stdin: str = "") -> str:
        result = subprocess.run(
            [*self.command, "exec", "-T", service, *command],
            input=stdin,
            encoding="utf-8",
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise AcceptanceError(
                f"Command failed in Compose service {service} with exit code "
                f"{result.returncode}; database output withheld"
            )
        return result.stdout.strip()

    def postgres(self, service: str, database: str, sql: str) -> str:
        return self.exec(
            service,
            [
                "psql",
                "-U",
                "postgres",
                "-d",
                database,
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            sql,
        )

    def mysql(self, service: str, database: str, sql: str) -> str:
        return self.exec(
            service,
            [
                "sh",
                "-ec",
                (
                    'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '
                    '--default-character-set=utf8mb4 -N -B "$0"'
                ),
                database,
            ],
            sql,
        )


def wait_for(
    operation: Callable[[], T],
    predicate: Callable[[T], bool],
    label: str,
    seconds: int = 180,
) -> T:
    deadline = time.monotonic() + seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = operation()
            if predicate(result):
                return result
        except (AcceptanceError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(1)
    detail = f" Last error: {last_error}" if last_error else ""
    raise AcceptanceError(f"Timed out waiting for {label}.{detail}")


def fixture_table_mapping(
    pipeline: Mapping[str, Any], delivery: Mapping[str, Any], table_name: str
) -> tuple[dict, dict]:
    """Select a fixture by identity, independent of API collection ordering."""
    tables = [
        table for table in pipeline["tables"] if table["table_name"] == table_name
    ]
    if len(tables) != 1:
        raise AcceptanceError("Expected exactly one matching source fixture table")
    mappings = [
        mapping
        for mapping in delivery["topic_mapping_json"]
        if mapping["topic"] == tables[0]["topic_name"]
    ]
    if len(mappings) != 1:
        raise AcceptanceError(
            "Expected exactly one matching fixture destination mapping"
        )
    return tables[0], mappings[0]


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value)).normalize()
    except InvalidOperation as error:
        raise AcceptanceError(
            f"Invalid decimal in reconciliation: {value!r}"
        ) from error


def _timestamp(value: object) -> datetime:
    text = str(value).replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def reconcile_records(
    source: Sequence[Mapping[str, object]],
    destination: Sequence[Mapping[str, object]],
    *,
    key_fields: Sequence[str],
    decimal_fields: Iterable[str] = (),
    timestamp_fields: Iterable[str] = (),
) -> None:
    """Compare materialized records and report bounded, field-level diagnostics."""

    decimals = set(decimal_fields)
    timestamps = set(timestamp_fields)

    def keyed(
        rows: Sequence[Mapping[str, object]],
    ) -> dict[tuple[object, ...], dict[str, object]]:
        result: dict[tuple[object, ...], dict[str, object]] = {}
        for raw in rows:
            row = dict(raw)
            key = tuple(row.get(field) for field in key_fields)
            if key in result:
                raise AcceptanceError(f"Duplicate reconciliation key: {key!r}")
            for field in decimals & row.keys():
                if row[field] is not None:
                    row[field] = _decimal(row[field])
            for field in timestamps & row.keys():
                if row[field] is not None:
                    row[field] = _timestamp(row[field])
            result[key] = row
        return result

    expected, actual = keyed(source), keyed(destination)
    missing = sorted(expected.keys() - actual.keys(), key=repr)
    unexpected = sorted(actual.keys() - expected.keys(), key=repr)
    differences: list[str] = []
    for key in sorted(expected.keys() & actual.keys(), key=repr):
        fields = sorted(expected[key].keys() | actual[key].keys())
        changed = [
            field
            for field in fields
            if expected[key].get(field) != actual[key].get(field)
        ]
        if changed:
            differences.append(f"key={key!r} fields={changed!r}")
    if missing or unexpected or differences:
        raise AcceptanceError(
            "CDC reconciliation mismatch: "
            f"missing={missing[:5]!r}, unexpected={unexpected[:5]!r}, "
            f"different={differences[:5]!r}"
        )
