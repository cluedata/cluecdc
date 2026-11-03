"""API-driven real CDC reliability acceptance, extending the existing acceptance utilities.

Build runtime images first, then run with --execute. Default is a truthful NOT RUN
matrix. Fault injection only accepts containers owned by this process's new project.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import uuid
from collections import Counter
from decimal import Decimal, localcontext
from datetime import UTC, date, datetime
from pathlib import Path

import boto3
import httpx
from cdc_acceptance_support import AcceptanceError
from cdc_reliability_support import (
    ROOT,
    DisposableStack,
    Evidence,
    api_for,
    history_identity,
    wait_value,
)

sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.errors import DomainError
from app.providers.source_capabilities import validate_delivery

AMOUNT = "12345678901234567890.123456789"
TEXT = "Tiếng Việt — dữ liệu"
PAIRS = [
    (s, d)
    for s in ("postgresql", "mysql")
    for d in ("POSTGRESQL" if s == "postgresql" else "MYSQL", "MINIO", "CLICKHOUSE")
]
EXTRA = [("mariadb", "POSTGRESQL"), ("sqlserver", "MINIO"), ("mongodb", "MINIO")]
CLOUD = [
    (s, d)
    for s in ("postgresql", "mysql")
    for d in ("BIGQUERY", "REDSHIFT", "AZURE_SYNAPSE")
]


def compatibility(evidence):
    rows = []
    for source in ("postgresql", "mysql", "mariadb", "sqlserver", "mongodb", "oracle"):
        for destination in (
            "POSTGRESQL",
            "MYSQL",
            "AWS_S3",
            "MINIO",
            "CLICKHOUSE",
            "BIGQUERY",
            "REDSHIFT",
            "AZURE_SYNAPSE",
        ):
            warehouse = destination in {
                "CLICKHOUSE",
                "BIGQUERY",
                "REDSHIFT",
                "AZURE_SYNAPSE",
            }
            for mode in (
                ["insert", "upsert"]
                if warehouse
                else ["insert"]
                if destination in {"AWS_S3", "MINIO"}
                else ["upsert"]
            ):
                try:
                    validate_delivery(source, destination, mode)
                    supported = not (warehouse and mode == "upsert")
                except DomainError:
                    supported = False
                results = [
                    r
                    for r in evidence.results
                    if r["source_type"] == source
                    and r["destination_type"] == destination
                    and r["delivery_mode"]
                    == ("CURRENT_STATE" if mode == "upsert" else "EVENT_LOG")
                ]
                core = [r for r in results if r["scenario"] == "crud"]
                status = (
                    "NOT SUPPORTED"
                    if not supported
                    else "PASS"
                    if any(r["status"] == "PASS" for r in core)
                    else "NOT RUN"
                )
                if supported and any(r["status"] == "FAIL" for r in results):
                    status = "FAIL"
                rows.append(
                    {
                        "source": source,
                        "destination": destination,
                        "write_mode": mode,
                        "delivery_mode": "CURRENT_STATE"
                        if mode == "upsert"
                        else "EVENT_LOG",
                        "category": "unsupported"
                        if not supported
                        else "verified_supported"
                        if status == "PASS"
                        else "candidate",
                        "e2e_status": status,
                        "capabilities": {
                            scenario: next(
                                (
                                    r["status"]
                                    for r in reversed(results)
                                    if r["scenario"] == scenario
                                ),
                                "NOT RUN",
                            )
                            for scenario in (
                                "connection",
                                "snapshot",
                                "crud",
                                "event-identity",
                                "recovery",
                            )
                        },
                        "reason": None
                        if status == "PASS"
                        else "Capability guard"
                        if not supported
                        else "See per-scenario evidence; no current live assertions",
                    }
                )
                capabilities = rows[-1]["capabilities"]
                capabilities.update(
                    {
                        "streaming": capabilities["crud"],
                        "insert": capabilities["crud"],
                        "update": capabilities["crud"],
                        "delete": capabilities["crud"],
                        "data_correctness": capabilities["crud"],
                        "replay": next(
                            (
                                r["status"]
                                for r in reversed(results)
                                if r["scenario"] == "replay"
                            ),
                            "NOT RUN",
                        ),
                    }
                )
                # The legacy source suite records multiple real assertions in
                # one result. Preserve their narrower scope in the matrix.
                adapter_result = next(
                    (
                        r
                        for r in reversed(core)
                        if r["status"] == "PASS"
                        and isinstance(r.get("observed_behavior"), dict)
                        and r["observed_behavior"].get("snapshot") == "PASS"
                    ),
                    None,
                )
                if adapter_result:
                    observed = adapter_result["observed_behavior"]
                    capabilities["connection"] = "PARTIAL"
                    capabilities["snapshot"] = "PASS"
                    if (
                        observed.get("outage_recovery")
                        == "PASS_WITH_EXISTING_TASK_RESTART"
                    ):
                        capabilities["recovery"] = "PASS"
                    rows[-1]["verification_scope"] = (
                        "Real adapter/Connect and destination assertions; "
                        "API connection orchestration unverified; "
                        "source outage recovery uses explicit task restart"
                    )
                final_correctness = next(
                    (
                        r
                        for r in reversed(results)
                        if r["scenario"] == "final-data-correctness"
                    ),
                    None,
                )
                if final_correctness:
                    capabilities["data_correctness"] = final_correctness["status"]
    evidence.output.mkdir(parents=True, exist_ok=True)
    (evidence.output / "compatibility-matrix.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )


def fixture(stack, source, table):
    binary = "BYTEA" if source == "postgresql" else "BLOB"
    raw = "decode('00ff','hex')" if source == "postgresql" else "UNHEX('00ff')"
    stack.sql(
        source,
        f"""CREATE TABLE {table} (
        tenant INT NOT NULL, id INT NOT NULL, code VARCHAR(64) UNIQUE NOT NULL,
        label VARCHAR(255), amount DECIMAL(30,9), optional_value VARCHAR(40),
        active BOOLEAN, occurred DATE, recorded TIMESTAMP(6), long_text TEXT,
        raw_data {binary}, PRIMARY KEY(tenant,id));
        INSERT INTO {table} VALUES (7,1,'seed','{TEXT}',{AMOUNT},NULL,
        TRUE,'2026-01-02','2026-01-02 03:04:05.123456','{"x" * 8192}',{raw});""",
    )
    if source == "postgresql":
        stack.sql(source, f"ALTER TABLE {table} REPLICA IDENTITY FULL")


def amount(value):
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    with localcontext() as context:
        context.prec = 50
        return Decimal(
            int.from_bytes(base64.b64decode(value), "big", signed=True)
        ).scaleb(-9)


def extra_source(stack, evidence, source):
    """Reuse source_e2e.py, including its source-specific BSON/CDC assertions."""
    if source == "sqlserver":
        from cdc_reliability_sqlserver import run_sqlserver

        return run_sqlserver(stack, evidence)
    stack.add_source(source)
    port = {"mariadb": 3306, "sqlserver": 1433, "mongodb": 27017}[source]
    host_port = int(stack.url(source + "-source", port).rsplit(":", 1)[1])

    def listener():
        try:
            with socket.create_connection(("127.0.0.1", host_port), timeout=1):
                return True
        except OSError:
            return False

    wait_value(listener, bool, "optional source listener", timeout=120)
    time.sleep(10)
    environment = {
        **os.environ,
        **stack.defaults,
        "SOURCE_E2E_PORT": str(host_port),
        "SOURCE_E2E_POSTGRES_PORT": stack.url("postgres-demo", 5432).rsplit(":", 1)[1],
        "SOURCE_E2E_MONGO_MEMBER": "mongodb-source:27017",
    }
    # Prevent existing host credentials/configuration from entering the fixture.
    environment.update(
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        AUTH_MODE="developer",
        ENVIRONMENT="test",
        BOOTSTRAP_DEFAULT_ADMIN="false",
        PYTHONIOENCODING="utf-8",
    )
    report = evidence.output.resolve() / (source + "-source.json")
    command = [
        sys.executable,
        str(ROOT / "scripts" / "source_e2e.py"),
        "--source",
        source,
        "--source-host",
        source + "-source",
        "--connect-url",
        stack.url("kafka-connect", 8083),
        "--minio-url",
        stack.url("minio", 9000),
        "--env-file",
        str(ROOT / ".env.test.example"),
        "--fixture-project",
        stack.project,
        "--outage-container",
        stack.project + "-" + source + "-source-1",
        "--report",
        str(report),
    ]
    if source == "mariadb":
        command.append("--stateful-postgres")
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
    )
    observed = json.loads(report.read_text()) if report.exists() else None
    destination = "POSTGRESQL" if source == "mariadb" else "MINIO"
    evidence.add(
        source + ":extended",
        source,
        destination,
        "CURRENT_STATE" if source == "mariadb" else "EVENT_LOG",
        "crud",
        "Existing real adapter/builder-to-MinIO CDC assertions and two operator-assisted source restarts; MariaDB also PostgreSQL current state",
        status="PASS" if result.returncode == 0 and observed else "FAIL",
        observed=observed,
        reason=None
        if result.returncode == 0
        else "Existing source runner failed; sanitized failure log attached",
        duration=time.monotonic() - started,
        tier="real adapter/Connect E2E (API orchestration unverified)",
    )
    if result.returncode:
        from cdc_reliability_support import redact

        (evidence.output / (source + "-failure.json")).write_text(
            json.dumps(
                redact(
                    {"stdout": result.stdout, "stderr": result.stderr}, evidence.secrets
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        raise AcceptanceError("Optional source acceptance failed: " + source)
    if source == "mariadb":
        evidence.add(
            source + ":minio",
            source,
            "MINIO",
            "EVENT_LOG",
            "crud",
            "Raw MinIO CRUD",
            observed=observed,
        )


class Scenario:
    def __init__(self, stack, evidence, source, destination, cloud=None):
        self.stack, self.evidence, self.source, self.destination = (
            stack,
            evidence,
            source,
            destination,
        )
        self.mode = (
            "CURRENT_STATE" if destination in {"POSTGRESQL", "MYSQL"} else "EVENT_LOG"
        )
        self.api = api_for(stack)
        self.table = (
            "cdc_" + stack.run_id + "_" + source[:2] + "_" + destination.lower()[:2]
        )
        self.schema = "public" if source == "postgresql" else "cluecdc_source"
        self.topic = self.table + "." + self.schema + "." + self.table
        self.pipeline = self.delivery = self.source_connection = (
            self.destination_connection
        ) = None
        self.expected = []
        self.connect = httpx.Client(
            base_url=stack.url("kafka-connect", 8083), timeout=20
        )
        self.s3 = None
        self.cloud = cloud
        self.snapshot_rows = 1
        self.restore_probe = None

    def record(self, scenario, expected, fn):
        started = time.monotonic()
        try:
            observed = fn()
        except Exception as error:
            self.evidence.add(
                self.table + ":" + scenario,
                self.source,
                self.destination,
                self.mode,
                scenario,
                expected,
                status="FAIL",
                observed={
                    "error": type(error).__name__,
                    "expected_operations": self.expected,
                    "assertion_location": str(
                        traceback.extract_tb(error.__traceback__)[-1]
                    ),
                },
                reason=str(error) or "Assertion failed during " + scenario,
                duration=time.monotonic() - started,
            )
            raise
        self.evidence.add(
            self.table + ":" + scenario,
            self.source,
            self.destination,
            self.mode,
            scenario,
            expected,
            observed=observed,
            duration=time.monotonic() - started,
            offsets={key: value for key, value in observed.items() if "offset" in key}
            if isinstance(observed, dict)
            else None,
        )
        return observed

    def setup(self):
        fixture(self.stack, self.source, self.table)
        if self.snapshot_rows > 1:
            assert self.source == "postgresql"
            self.stack.sql(
                self.source,
                f"INSERT INTO {self.table}(tenant,id,code,label,amount) SELECT 7,1000+n,'bulk-'||n,'snapshot-'||n,1 FROM generate_series(1,{self.snapshot_rows - 1}) n",
            )
        password = self.stack.defaults[
            "POSTGRES_DEMO_ADMIN_PASSWORD"
            if self.source == "postgresql"
            else "MYSQL_DEMO_ADMIN_PASSWORD"
        ]
        self.source_connection = self.api.create(
            "/sources",
            {
                "name": self.table,
                "type": self.source,
                "host": "postgres-demo"
                if self.source == "postgresql"
                else "mysql-demo",
                "port": 5432 if self.source == "postgresql" else 3306,
                "database_name": "cluecdc_source",
                "username": "postgres" if self.source == "postgresql" else "root",
                "password": password,
            },
        )
        source_id = self.source_connection["id"]
        tested = self.api.request(f"/sources/{source_id}/test", "POST")
        assert tested.get("status") in {
            "HEALTHY",
            "healthy",
            "success",
            "passed",
            "warning",
        }
        readiness = self.api.request(f"/sources/{source_id}/cdc-readiness")
        assert readiness.get("status") != "failed"
        job = self.api.create(f"/sources/{source_id}/discover", {})
        wait_value(
            lambda: self.api.request(f"/jobs/{job['id']}"),
            lambda j: j["status"] in {"COMPLETED", "FAILED"},
            "discovery",
        )
        assert self.api.request(f"/jobs/{job['id']}")["status"] == "COMPLETED"
        kafka = self.api.create(
            "/kafka/clusters", {"name": self.table, "bootstrap_servers": "kafka:29092"}
        )
        connect = self.api.create(
            "/connect/clusters",
            {
                "name": self.table,
                "base_url": "http://kafka-connect:8083",
                "kafka_cluster_id": kafka["id"],
            },
        )
        payload = {
            "name": self.table,
            "source_id": source_id,
            "kafka_cluster_id": kafka["id"],
            "connect_cluster_id": connect["id"],
            "topic_prefix": self.table,
            "snapshot_mode": "initial",
            "tables": [{"schema_name": self.schema, "table_name": self.table}],
        }
        if self.snapshot_rows > 1:
            payload.update(
                max_batch_size=1,
                max_queue_size=2,
                poll_interval_ms=100,
                additional_debezium_properties={"snapshot.fetch.size": "1"},
            )
        preview = self.api.create("/pipelines/preview", payload)
        assert preview["config"]["database.password"] == "[REDACTED]"
        self.pipeline = self.api.create("/pipelines", payload)
        self.api.create(f"/pipelines/{self.pipeline['id']}/deploy", {})
        self.wait_capture("RUNNING")
        self.deploy_destination()
        return {
            "readiness": readiness.get("status"),
            "source_id": source_id,
            "pipeline_id": self.pipeline["id"],
            "api_preview": "redacted",
        }

    def deploy_destination(self):
        config, credentials = {}, {}
        if self.destination in {"POSTGRESQL", "MYSQL"}:
            target = self.destination.lower()
            config = {
                "host": "postgres-demo" if target == "postgresql" else "mysql-demo",
                "port": 5432 if target == "postgresql" else 3306,
                "database_name": "cluecdc_destination",
                "username": "postgres" if target == "postgresql" else "root",
                "ssl_enabled": False,
            }
            credentials = {
                "password": self.stack.defaults[
                    "POSTGRES_DEMO_ADMIN_PASSWORD"
                    if target == "postgresql"
                    else "MYSQL_DEMO_ADMIN_PASSWORD"
                ]
            }
            schema = "public" if target == "postgresql" else "cluecdc_destination"
        elif self.destination == "MINIO":
            self.bucket = "cdc-" + self.table.replace("_", "-")
            self.s3 = boto3.client(
                "s3",
                endpoint_url=self.stack.url("minio", 9000),
                aws_access_key_id=self.stack.defaults["MINIO_ACCESS_KEY"],
                aws_secret_access_key=self.stack.defaults["MINIO_SECRET_KEY"],
                region_name="us-east-1",
            )
            self.s3.create_bucket(Bucket=self.bucket)
            config = {
                "bucket": self.bucket,
                "region": "us-east-1",
                "endpoint": "http://minio:9000",
            }
            credentials = {
                "access_key": self.stack.defaults["MINIO_ACCESS_KEY"],
                "secret_key": self.stack.defaults["MINIO_SECRET_KEY"],
            }
            schema = "public"
        elif self.cloud:
            spec = self.cloud[self.destination]
            config, credentials = dict(spec["config"]), dict(spec["credentials"])
            schema = (
                config.get("dataset_id")
                or config.get("schema_name")
                or config["target_schema"]
            )
            if "staging_prefix" in config:
                config["staging_prefix"] = (
                    config["staging_prefix"].rstrip("/") + "/" + self.stack.run_id
                )
        else:
            config = {
                "host": "clickhouse",
                "port": 8123,
                "database_name": "default",
                "username": "cdc_test",
                "ssl_enabled": False,
            }
            credentials = {"password": "isolated-cdc-only"}
            schema = "default"
        self.destination_connection = self.api.create(
            "/connections",
            {
                "name": self.table + "_dest",
                "provider": self.destination,
                "category": "OBJECT_STORAGE"
                if self.destination == "MINIO"
                else "DATABASE",
                "capabilities": ["DESTINATION"],
                "config": config,
                "credentials": credentials,
            },
        )
        dest_id = self.destination_connection["id"]
        tested = self.api.create(f"/destinations/{dest_id}/test", {})
        assert tested.get("success", tested.get("status") in {"HEALTHY", "passed"})
        payload = {
            "pipeline_id": self.pipeline["id"],
            "write_mode": "upsert" if self.mode == "CURRENT_STATE" else "insert",
            "mappings": [
                {"topic": self.topic, "schema_name": schema, "table_name": self.table}
            ],
            "auto_create": True,
            "auto_evolve": False,
        }
        if self.destination == "MINIO":
            payload.pop("mappings")
            payload.update(
                delivery_type="OBJECT_STORAGE",
                topics=[self.topic],
                compression="none",
                file_max_records=1,
                flush_interval_ms=1000,
            )
        self.api.create(f"/destinations/{dest_id}/preview", payload)
        self.delivery = self.api.create(f"/destinations/{dest_id}/deploy", payload)

    def wait_capture(self, expected):
        return wait_value(
            lambda: self.api.request(f"/pipelines/{self.pipeline['id']}/status"),
            lambda s: (
                s.get("connector", {}).get("state") == expected
                and bool(s.get("tasks"))
                and all(t["state"] == expected for t in s["tasks"])
            ),
            "capture " + expected,
        )

    def events(self):
        return self.stack.kafka_events(self.topic)

    def rows(self):
        if self.cloud:
            return self.cloud_rows()
        if self.destination == "CLICKHOUSE":
            response = httpx.post(
                self.stack.url("clickhouse", 8123),
                auth=("cdc_test", "isolated-cdc-only"),
                content=f"SELECT * FROM default.{self.table} FORMAT JSONEachRow",
                timeout=10,
            )
            if response.status_code != 200:
                return []
            return [json.loads(line) for line in response.text.splitlines()]
        if self.destination == "MINIO":
            records = []
            for page in self.s3.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket
            ):
                for obj in page.get("Contents", []):
                    raw = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])[
                        "Body"
                    ].read()
                    if obj["Key"].endswith(".gz"):
                        raw = gzip.decompress(raw)
                    for line in raw.splitlines():
                        record = json.loads(line)
                        value = record.get("value")
                        if isinstance(value, str):
                            value = json.loads(value)
                        if isinstance(value, dict):
                            records.append(value.get("payload", value))
            return records
        return self.state_rows(self.destination, "cluecdc_destination")

    def state_rows(self, provider, database):
        if provider == "POSTGRESQL":
            raw = self.stack.sql(
                "postgresql",
                f"SELECT coalesce(json_agg(t),'[]') FROM (SELECT tenant,id,code,label,amount::text,optional_value,active,occurred::text,recorded::text,long_text,encode(raw_data,'hex') raw_data FROM {self.table} ORDER BY tenant,id) t",
                database,
            )
            return json.loads(raw)
        raw = self.stack.sql(
            "mysql",
            f"SELECT JSON_OBJECT('tenant',tenant,'id',id,'code',code,'label',label,'amount',CAST(amount AS CHAR),'optional_value',optional_value,'active',active,'occurred',CAST(occurred AS CHAR),'recorded',CAST(recorded AS CHAR),'long_text',long_text,'raw_data',HEX(raw_data)) FROM {self.table} ORDER BY tenant,id",
            database,
        )
        return [json.loads(line) for line in raw.splitlines()]

    def final_correctness(self):
        if self.mode == "CURRENT_STATE":
            expected = self.state_rows(self.source.upper(), "cluecdc_source")
            actual = wait_value(
                self.rows,
                lambda rows: rows == expected,
                "complete final source/destination equality",
            )
            time.sleep(2)
            assert self.rows() == actual
            return {
                "records": len(actual),
                "all_columns_equal": True,
                "sha256": hashlib.sha256(
                    json.dumps(actual, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
            }
        if self.destination in {"CLICKHOUSE", "BIGQUERY", "REDSHIFT", "AZURE_SYNAPSE"}:
            return self.identity()
        expected = Counter(
            json.dumps(r["value"], sort_keys=True) for r in self.events() if r["value"]
        )

        def contains_all(rows):
            actual = Counter(json.dumps(r, sort_keys=True) for r in rows)
            return all(actual[key] >= count for key, count in expected.items())

        rows = wait_value(
            self.rows, contains_all, "all captured Kafka values preserved in MinIO"
        )
        actual = Counter(json.dumps(r, sort_keys=True) for r in rows)
        assert set(actual) == set(expected)
        return {
            "kafka_events": sum(expected.values()),
            "destination_events": len(rows),
            "additional_delivery_occurrences": sum(actual.values())
            - sum(expected.values()),
            "scope": "all event values match; raw MinIO has no warehouse coordinate-column contract",
        }

    def delivered(self, label, identity=None):
        def contains(rows):
            for row in rows:
                body = (
                    json.loads(row["_clue_after"])
                    if self.mode == "EVENT_LOG"
                    and self.destination != "MINIO"
                    and row.get("_clue_after")
                    else row.get("after")
                    if self.destination == "MINIO"
                    else row
                )
                if (
                    body
                    and body.get("label") == label
                    and (identity is None or body.get("id") == identity)
                ):
                    return True
            return False

        return wait_value(self.rows, contains, "destination marker " + label)

    def snapshot(self):
        events = wait_value(
            self.events,
            lambda rows: any(
                (r["value"] or {}).get("source", {}).get("snapshot")
                in {"last", "true", True}
                and (r["value"] or {}).get("op") == "r"
                for r in rows
            ),
            "observable snapshot records",
        )
        # Completion requires last=true notification in a snapshot row OR a saved
        # source offset with snapshot no longer in progress; RUNNING is insufficient.
        connector = self.api.request(f"/pipelines/{self.pipeline['id']}")["connector"][
            "name"
        ]

        def complete():
            response = self.connect.get(f"/connectors/{connector}/offsets")
            response.raise_for_status()
            data = response.json()
            return (
                data
                if data.get("offsets")
                and all(
                    o["offset"].get("snapshot") not in {True, "true"}
                    for o in data["offsets"]
                )
                else None
            )

        offsets = wait_value(complete, bool, "snapshot offset completion")
        snapshot = next(r for r in events if (r["value"] or {}).get("op") == "r")
        body = snapshot["value"]["after"]
        assert snapshot["key"] == {"tenant": 7, "id": 1}
        assert body["label"] == TEXT and amount(body["amount"]) == Decimal(AMOUNT)
        assert body["optional_value"] is None and body["active"] in {True, 1}
        assert body["occurred"] == (date(2026, 1, 2) - date(1970, 1, 1)).days
        expected_timestamp = int(
            datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC).timestamp() * 1_000_000
        )
        recorded = body["recorded"]
        if isinstance(recorded, str):
            assert datetime.fromisoformat(recorded.replace("Z", "+00:00")) == datetime(
                2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC
            )
        else:
            assert recorded == expected_timestamp
        assert (
            body["long_text"] == "x" * 8192
            and base64.b64decode(body["raw_data"]) == b"\x00\xff"
        )
        rows = self.delivered(TEXT, 1)
        if self.mode == "CURRENT_STATE":
            row = next(r for r in rows if r["id"] == 1)
            assert (
                row["amount"] == AMOUNT
                and row["label"] == TEXT
                and row["optional_value"] is None
            )
            assert row["raw_data"].lower() == "00ff" and row["long_text"] == "x" * 8192
            assert row["occurred"] == "2026-01-02" and row["recorded"].startswith(
                "2026-01-02 03:04:05.123456"
            )
        return {
            "snapshot_complete_offsets": offsets,
            "key": snapshot["key"],
            "types": [
                "composite key",
                "Unicode",
                "decimal(30,9)",
                "null",
                "boolean",
                "binary",
                "large text",
            ],
            "expected": body,
        }

    def mutate(self, identity, label):
        self.expected.append({"op": "c", "tenant": 7, "id": identity, "label": label})
        self.stack.sql(
            self.source,
            f"INSERT INTO {self.table}(tenant,id,code,label,amount) VALUES(7,{identity},'code{identity}','{label}',2.000000001)",
        )

    def crud(self):
        self.mutate(2, "insert")
        self.delivered("insert", 2)
        self.expected.append(
            {
                "op": "u",
                "id": 2,
                "label": "updated",
                "optional_value": "set",
                "amount": "9.123456789",
            }
        )
        self.stack.sql(
            self.source,
            f"UPDATE {self.table} SET label='updated',optional_value='set',amount=9.123456789 WHERE tenant=7 AND id=2",
        )
        self.delivered("updated", 2)
        self.expected.append({"op": "d", "id": 2})
        self.stack.sql(self.source, f"DELETE FROM {self.table} WHERE tenant=7 AND id=2")
        events = wait_value(
            self.events,
            lambda rows: (
                {"r", "c", "u", "d"} <= {(r["value"] or {}).get("op") for r in rows}
            ),
            "Kafka CRUD",
        )
        update = next(r for r in events if (r["value"] or {}).get("op") == "u")
        delete = next(r for r in events if (r["value"] or {}).get("op") == "d")
        assert update["value"]["before"]["label"] == "insert"
        assert update["value"]["after"]["label"] == "updated"
        assert amount(update["value"]["after"]["amount"]) == Decimal("9.123456789")
        assert (
            delete["key"] == {"tenant": 7, "id": 2} and delete["value"]["after"] is None
        )
        if self.mode == "CURRENT_STATE":
            rows = wait_value(
                self.rows,
                lambda rows: [r["id"] for r in rows] == [1],
                "delete final-state equality",
            )
            # Bound the final-state settle check rather than accepting a momentary match.
            time.sleep(2)
            assert self.rows() == rows
        else:
            wait_value(
                self.rows,
                lambda rows: (
                    {"r", "c", "u", "d"}
                    <= {r.get("_clue_operation", r.get("op")) for r in rows}
                ),
                "destination history CRUD",
            )
        return {
            "expected_operations": self.expected,
            "kafka_events": events,
            "destination_rows": len(self.rows()),
            "delete_semantics": "propagated"
            if self.mode == "CURRENT_STATE"
            else "preserved event",
        }

    def identity(self):
        if self.destination not in {
            "CLICKHOUSE",
            "BIGQUERY",
            "REDSHIFT",
            "AZURE_SYNAPSE",
        }:
            return {
                "scope": "Kafka coordinates captured; MinIO raw format does not promise warehouse coordinate columns"
            }
        events = [r for r in self.events() if r["value"]]
        rows = self.rows()
        keyed = {
            (r["_clue_topic"], int(r["_clue_partition"]), int(r["_clue_offset"])): r
            for r in rows
        }
        for event in events:
            row = keyed[(event["topic"], event["partition"], event["offset"])]
            assert row["_clue_operation"] == event["value"]["op"]
            assert json.loads(row["_clue_key"]) == event["key"]
            for field in ("source", "before", "after"):
                value = row["_clue_" + field]
                assert (json.loads(value) if value is not None else None) == event[
                    "value"
                ].get(field)
        return history_identity(rows)

    def cloud_rows(self):
        # SDK reads execute inside the fixture image, with credentials on stdin,
        # never command-line arguments or evidence. No dataset/pool is provisioned.
        script = """import json,sys
from app.models.entities import Connection
p=json.load(sys.stdin)
c=Connection(name="test",provider=p["provider"],category="DATABASE",config_json=p["config"])
credentials=p["credentials"]
table=p["table"]
if p["provider"]=="BIGQUERY":
 from app.services.bigquery import _client
 client=_client(c,credentials)
 try:
  rows=[dict(r) for r in client.query("SELECT * FROM `"+p["config"]["project_id"]+"."+p["config"]["dataset_id"]+"."+table+"`").result(timeout=60)]
 finally: client.close()
else:
 if p["provider"]=="REDSHIFT":
  import redshift_connector
  from app.services.redshift import connection_options
  from app.services.redshift_runtime import _quote
  db=redshift_connector.connect(**connection_options(c,credentials))
  schema=p["config"]["schema_name"]
 else:
  import mssql_python
  from app.services.azure_synapse.connection import _connection_string
  from app.services.azure_synapse.runtime import _quote
  db=mssql_python.connect(_connection_string(c,credentials),timeout=30)
  schema=p["config"]["target_schema"]
 try:
  cursor=db.cursor()
  cursor.execute("SELECT * FROM "+_quote(schema)+"."+_quote(table))
  names=[d[0] for d in cursor.description]
  rows=[dict(zip(names,row)) for row in cursor.fetchall()]
  cursor.close()
 finally: db.close()
print(json.dumps(rows,default=str))
"""
        spec = self.cloud[self.destination]
        return json.loads(
            self.stack.exec(
                "cluecdc-api",
                ["python", "-c", script],
                json.dumps({**spec, "provider": self.destination, "table": self.table}),
            )
        )

    def lifecycle(self):
        identifier = self.pipeline["id"]
        for operation, state in (
            ("pause", "PAUSED"),
            ("resume", "RUNNING"),
            ("restart", "RUNNING"),
        ):
            self.api.create(f"/pipelines/{identifier}/{operation}", {})
            self.wait_capture(state)
        self.mutate(3, "after-restart")
        self.delivered("after-restart", 3)
        audit = self.api.request("/audit?resource_id=" + identifier)
        actions = {r["action"] for r in audit}
        assert {
            "pipeline.created",
            "pipeline.deployed",
            "pipeline.paused",
            "pipeline.resumed",
            "pipeline.restarted",
        } <= actions
        return {
            "actions": sorted(actions),
            "recovery": "explicit operator API restart",
            "identity": self.identity(),
        }

    def replay(self):
        original = self.events()
        state = self.rows()
        self.stack.replay(self.topic, original)
        wait_value(
            self.events,
            lambda rows: len(rows) >= 2 * len(original),
            "ordered test replay in Kafka",
        )
        self.api.create(f"/deliveries/{self.delivery['id']}/restart", {})
        if self.mode == "CURRENT_STATE":
            wait_value(
                self.rows,
                lambda rows: rows == state,
                "replayed insert/update/delete final equality",
            )
            time.sleep(3)
            assert self.rows() == state
            observed = {
                "final_state_equal": True,
                "ordering": "original per-partition order only; arbitrary old-event ordering unverified",
            }
        else:

            def snapshot_count(rows):
                return sum(r.get("_clue_operation", r.get("op")) == "r" for r in rows)

            wait_value(
                self.rows,
                lambda rows: snapshot_count(rows) >= 2,
                "replayed snapshot delivered",
            )
            observed = self.identity()
            observed["logical_snapshot_occurrences"] = snapshot_count(self.rows())
        return {
            "deliberately_replayed_records": len(original),
            "sink_restart": "existing delivery API",
            **observed,
        }

    def recover_fault(self, service, index):
        connector = self.api.request(f"/pipelines/{self.pipeline['id']}")["connector"][
            "name"
        ]
        before = self.connect.get(f"/connectors/{connector}/offsets").json()
        assert before.get("offsets")
        config = self.connect.get(f"/connectors/{connector}/config").json()
        names = sorted(self.connect.get("/connectors").json())
        self.stack.fault(service, "stop")
        try:
            if service in {"clickhouse", "minio", "kafka", "kafka-connect"}:
                self.mutate(index, "during-outage")
            if service in {"clickhouse", "minio"}:
                wait_value(
                    self.events,
                    lambda rows: any(
                        (r["value"] or {}).get("after", {}).get("label")
                        == "during-outage"
                        for r in rows
                        if (r["value"] or {}).get("after")
                    ),
                    "capture independent of destination",
                )
            time.sleep(3)
        finally:
            self.stack.fault(service, "start")
        self.stack.wait_service(service)
        if callable(self.restore_probe) and service.endswith("-source"):
            wait_value(
                self.restore_probe,
                bool,
                "restored source database readiness",
                timeout=120,
            )
        manual = False
        # Observe automatic recovery first, then use the existing explicit action
        # only if a connector has actually failed. Never recreate or reset it.
        time.sleep(3)

        def resume():
            nonlocal manual
            try:
                status = self.connect.get(f"/connectors/{connector}/status").json()
                if any(t["state"] == "FAILED" for t in status.get("tasks", [])):
                    self.api.create(f"/pipelines/{self.pipeline['id']}/restart", {})
                    manual = True
                return status.get("connector", {}).get("state") == "RUNNING"
            except (httpx.HTTPError, ValueError):
                return False

        wait_value(resume, bool, "restored capture")
        self.mutate(index + 1, "after-" + service)
        self.delivered("after-" + service, index + 1)
        if service in {"clickhouse", "minio", "kafka", "kafka-connect"}:
            self.delivered("during-outage", index)

        def available_json(path):
            response = self.connect.get(path)
            if response.is_error:
                raise AcceptanceError(
                    f"Connect read {path} temporarily returned {response.status_code}"
                )
            return response.json()

        after_names = sorted(
            wait_value(
                lambda: available_json("/connectors"),
                bool,
                "Connect inventory restored",
            )
        )
        assert after_names == names, (
            f"Connector inventory changed: missing={set(names) - set(after_names)}, added={set(after_names) - set(names)}"
        )
        after_config = wait_value(
            lambda: available_json(f"/connectors/{connector}/config"),
            bool,
            "Connect config readable after recovery",
        )
        changed = sorted(
            k
            for k in set(config) | set(after_config)
            if config.get(k) != after_config.get(k)
        )
        assert not changed, "Source configuration changed fields: " + ", ".join(changed)
        after = self.connect.get(f"/connectors/{connector}/offsets").json()
        assert after.get("offsets")
        return {
            "service": service,
            "before_offsets": before,
            "after_offsets": after,
            "recovery": "operator API restart"
            if manual
            else "automatic (no test restart)",
            "connectors_preserved": True,
            "configuration_preserved": True,
            "identity": self.identity(),
        }

    def interrupt_snapshot(self):
        self.snapshot_rows = 50000
        self.setup()
        name = self.api.request(f"/pipelines/{self.pipeline['id']}")["connector"][
            "name"
        ]
        script = """import json,sys
from confluent_kafka import Consumer,TopicPartition
t=sys.stdin.read()
c=Consumer({"bootstrap.servers":"kafka:29092","group.id":"snapshot-inspect","enable.auto.commit":False,"enable.auto.offset.store":False})
try:
 lo,hi=c.get_watermark_offsets(TopicPartition(t,0),timeout=5)
 if hi:
  c.assign([TopicPartition(t,0,hi-1)])
  m=c.poll(5)
  v=json.loads(m.value()) if m and m.value() else {}
  v=v.get("payload",v)
  print(json.dumps({"high":hi,"operation":v.get("op"),"snapshot":v.get("source",{}).get("snapshot")}))
 else: print('{}')
finally: c.close()
"""
        progress = wait_value(
            lambda: json.loads(
                self.stack.exec(
                    "cluecdc-api", ["python", "-c", script], self.topic, timeout=15
                )
            ),
            lambda p: p.get("snapshot") in {True, "true"} and p.get("operation") == "r",
            "snapshot demonstrably in progress",
            timeout=60,
        )
        before = self.connect.get(f"/connectors/{name}/offsets").json()
        self.mutate(2, "during-snapshot")
        self.expected.append(
            {"op": "u", "tenant": 7, "id": 1, "label": "snapshot-concurrent-update"}
        )
        self.stack.sql(
            "postgresql",
            f"UPDATE {self.table} SET label='snapshot-concurrent-update' WHERE tenant=7 AND id=1",
        )
        self.api.create(f"/pipelines/{self.pipeline['id']}/restart", {})
        self.wait_capture("RUNNING")
        query = f"SELECT count(*), md5(string_agg(tenant::text||':'||id::text||':'||label,',' ORDER BY tenant,id)) FROM {self.table}"
        expected = self.stack.sql("postgresql", query)
        wait_value(
            lambda: self.stack.sql("postgresql", query, "cluecdc_destination"),
            lambda actual: actual == expected,
            "interrupted snapshot exact count and row/key digest",
            timeout=300,
        )
        self.mutate(3, "after-interrupted-snapshot")
        self.delivered("after-interrupted-snapshot", 3)
        return {
            "rows_verified": self.snapshot_rows + 1,
            "concurrent_source_operations": self.expected,
            "observed_before_interrupt": progress,
            "source_offsets_before": before,
            "source_offsets_after": self.connect.get(
                f"/connectors/{name}/offsets"
            ).json(),
            "destination_count_and_digest": expected,
            "recovery": "explicit API connector restart; PostgreSQL may replay snapshot; JDBC upserts settle to exact current state",
            "scope": "PostgreSQL initial snapshot only; no cross-source resume claim",
        }

    def expire_source_history(self):
        self.setup()
        self.snapshot()
        name = self.api.request(f"/pipelines/{self.pipeline['id']}")["connector"][
            "name"
        ]
        config = self.connect.get(f"/connectors/{name}/config").json()
        self.api.create(f"/pipelines/{self.pipeline['id']}/pause", {})
        self.wait_capture("PAUSED")
        # The pipeline API exposes pause/resume. Stop the owned Connect task
        # while its desired state stays paused, so no source reader retains WAL.
        self.connect.put(f"/connectors/{name}/stop").raise_for_status()
        wait_value(
            lambda: self.connect.get(f"/connectors/{name}/status").json(),
            lambda s: s["connector"]["state"] == "STOPPED",
            "source fully stopped",
        )
        before = self.connect.get(f"/connectors/{name}/offsets").json()
        assert before.get("offsets")
        # Alter only this run's owned PostgreSQL fixture. Never drop/advance a slot
        # or change a saved source offset to manufacture recovery.
        self.stack.owned_container("postgres-demo")
        self.stack.sql("postgresql", "ALTER SYSTEM SET max_slot_wal_keep_size='1MB'")
        self.stack.sql("postgresql", "SELECT pg_reload_conf()")
        self.stack.sql(
            "postgresql", f"CREATE TABLE {self.table}_wal(id INT, payload TEXT)"
        )
        slot = config["slot.name"]
        assert slot.replace("_", "").isalnum()
        status = None
        for cycle in range(4):
            self.stack.sql(
                "postgresql",
                f"INSERT INTO {self.table}_wal SELECT n,string_agg(md5(n::text||':'||g::text||':{cycle}'),'') FROM generate_series(1,1024)n CROSS JOIN generate_series(1,1024)g GROUP BY n",
            )
            self.stack.sql("postgresql", "SELECT pg_switch_wal(); CHECKPOINT")
            status = self.stack.sql(
                "postgresql",
                f"SELECT wal_status||':'||coalesce(invalidation_reason,'') FROM pg_replication_slots WHERE slot_name='{slot}'",
            )
            if status.startswith("lost:"):
                break
        assert status and status.startswith("lost:"), (
            "Disposable WAL expiry was not reproduced"
        )
        self.api.create(f"/pipelines/{self.pipeline['id']}/resume", {})
        observed = wait_value(
            lambda: self.api.request(f"/pipelines/{self.pipeline['id']}/status"),
            lambda s: (
                s.get("error", {}).get("code") == "SOURCE_RECOVERY_REQUIRED"
                or any(t.get("state") == "FAILED" for t in s.get("tasks", []))
            ),
            "unusable source position diagnostic",
            timeout=120,
        )
        assert observed.get("actual_state") == "FAILED"
        assert observed.get("error", {}).get("code") == "SOURCE_RECOVERY_REQUIRED"
        assert observed["error"]["retryable"] is False
        after = self.connect.get(f"/connectors/{name}/offsets").json()
        assert after == before
        assert self.connect.get(f"/connectors/{name}/config").json() == config
        return {
            "wal_status": status,
            "source_offsets_before": before,
            "source_offsets_after": after,
            "diagnostics": {
                "error": observed["error"],
                "actual_connect_tasks": observed["tasks"],
            },
            "recovery": "explicit operator reconciliation required; no reset, slot drop, or resnapshot performed",
        }

    def run(self, chaos=False):
        try:
            self.record(
                "connection",
                "API login, CRUD, test/readiness, discovery, previews, deploy",
                self.setup,
            )
            self.record(
                "snapshot",
                "Exact deterministic seed values; snapshot position complete",
                self.snapshot,
            )
            self.record(
                "crud",
                "Snapshot/insert/update/delete match Kafka and delivery semantics",
                self.crud,
            )
            self.record(
                "event-identity",
                "Preserve history coordinates and payloads; measure duplicates",
                self.identity,
            )
            self.record(
                "recovery",
                "Pause/resume/restart through existing APIs, audit and continued delivery",
                self.lifecycle,
            )
            self.record(
                "replay",
                "Ordered fixture events replay safely through the existing sink; report append-only duplicates",
                self.replay,
            )
            if chaos:
                services = ["kafka-connect", "kafka", "cluecdc-api", "cluecdc-worker"]
                if self.destination in {"CLICKHOUSE", "MINIO"}:
                    services += [self.destination.lower()]
                services += [
                    "postgres-demo" if self.source == "postgresql" else "mysql-demo"
                ] * 2
                for index, service in enumerate(services):
                    self.record(
                        "outage-recovery-" + str(index),
                        "Owned service interruption preserves positions and delivers post-recovery marker",
                        lambda service=service, index=index: self.recover_fault(
                            service, 10 + index * 2
                        ),
                    )
            self.record(
                "final-data-correctness",
                "Verify every final source row or captured event after all faults",
                self.final_correctness,
            )
        finally:
            self.connect.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--chaos", action="store_true")
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=["mariadb", "sqlserver", "mongodb"],
        default=["mariadb", "sqlserver", "mongodb"],
        help="Optional source subset for --extra-sources",
    )
    parser.add_argument(
        "--source-history-expiration",
        action="store_true",
        help="Expire retained WAL only in this run's disposable PostgreSQL fixture",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Run existing demos/effective-key/Playwright on owned services",
    )
    parser.add_argument("--web-image", default="cluecdc-reliability-web:local")
    parser.add_argument(
        "--snapshot-interruption",
        action="store_true",
        help="Interrupt a 50,000-row PostgreSQL snapshot; verify exact JDBC state",
    )
    parser.add_argument(
        "--extra-sources",
        action="store_true",
        help="Reuse existing MariaDB/SQL Server/MongoDB acceptance on owned fixtures",
    )
    parser.add_argument(
        "--cloud-config",
        type=Path,
        help="Explicit dedicated test config; authorize_test_writes must be true",
    )
    parser.add_argument("--retain-fixtures", action="store_true")
    parser.add_argument(
        "--pairs", default="all", help="all or comma-separated source:DESTINATION"
    )
    parser.add_argument("--api-image", default="cluecdc-reliability-api:local")
    parser.add_argument("--connect-image", default="cluecdc-reliability-connect:local")
    parser.add_argument("--minio-image", default="cluecdc-reliability-minio:local")
    parser.add_argument(
        "--output",
        type=Path,
        help="Evidence directory; defaults to an independent run directory",
    )
    args = parser.parse_args()
    args.output = (
        args.output or ROOT / "artifacts" / "cdc-reliability" / uuid.uuid4().hex[:12]
    )
    print("Evidence directory: " + str(args.output), flush=True)
    cloud = None
    if args.cloud_config:
        cloud = json.loads(args.cloud_config.read_text(encoding="utf-8"))
        if cloud.pop("authorize_test_writes", False) is not True:
            parser.error(
                "Cloud execution requires authorize_test_writes=true for dedicated test resources"
            )
        if args.chaos:
            parser.error(
                "Cloud runner supports lifecycle/replay checks; disposable outage injection is local only"
            )
    evidence = Evidence(
        args.output, "isolated Docker Compose" if args.execute else "not executed"
    )
    selected = (
        PAIRS
        if args.pairs == "all"
        else [tuple(p.split(":")) for p in args.pairs.split(",")]
    )
    if args.pairs == "none":
        selected = []
    if any(
        pair not in PAIRS and not (cloud and pair in CLOUD and pair[1] in cloud)
        for pair in selected
    ):
        parser.error(
            "Use a supported local relational pair; extended/cloud runners are documented separately"
        )
    for source, destination in EXTRA + CLOUD + [("oracle", "MINIO")]:
        if (
            args.extra_sources
            and source in args.sources
            and (source, destination) in EXTRA
        ):
            continue
        if (source, destination) in selected:
            continue
        evidence.add(
            f"{source}:{destination}",
            source,
            destination,
            "CURRENT_STATE" if destination == "POSTGRESQL" else "EVENT_LOG",
            "crud",
            "Real source-to-destination CDC assertions",
            status="NOT RUN",
            reason="Real cloud infrastructure unavailable"
            if (source, destination) in CLOUD
            else "Approved Oracle database unavailable"
            if source == "oracle"
            else "Use scripts/source_e2e.py with isolated optional source fixtures",
        )
    failure = False
    if not args.execute:
        for source, destination in selected:
            evidence.add(
                f"{source}:{destination}",
                source,
                destination,
                "CURRENT_STATE"
                if destination in {"POSTGRESQL", "MYSQL"}
                else "EVENT_LOG",
                "crud",
                "Real CDC",
                status="NOT RUN",
                reason="Execution requires --execute; no services started",
            )
    else:
        try:
            stack = DisposableStack(
                args.api_image,
                args.connect_image,
                args.minio_image,
                retain=args.retain_fixtures,
            )
        except Exception as error:
            evidence.add(
                "infrastructure",
                None,
                None,
                None,
                "provision",
                "Provision isolated fixture configuration",
                status="BLOCKED",
                reason=str(error),
            )
            compatibility(evidence)
            evidence.write()
            return 1
        evidence.environment = stack.project
        evidence.secrets = [
            v
            for k, v in stack.defaults.items()
            if any(n in k for n in ("PASSWORD", "SECRET", "TOKEN", "KEY"))
        ] + [
            "isolated-cdc-only",
            "local-cdc-fixture-password",
            "Local-CDC-Fixture-Password-123!",
        ]
        if cloud:
            for spec in cloud.values():
                evidence.secrets += list(spec["credentials"].values())
        try:
            stack.start()
            evidence.versions = {
                "images": json.loads(stack.compose("images", "--format", "json")),
                "connect_plugins": httpx.get(
                    stack.url("kafka-connect", 8083) + "/connector-plugins", timeout=20
                ).json(),
            }
            for source, destination in selected:
                print(
                    f"Running {source} -> {destination} in {stack.project}", flush=True
                )
                try:
                    Scenario(
                        stack,
                        evidence,
                        source,
                        destination,
                        cloud if (source, destination) in CLOUD else None,
                    ).run(args.chaos)
                except Exception as error:
                    failure = True
                    if not any(
                        r["source_type"] == source
                        and r["destination_type"] == destination
                        and r["status"] == "FAIL"
                        for r in evidence.results
                    ):
                        evidence.add(
                            f"{source}:{destination}:initialization",
                            source,
                            destination,
                            "CURRENT_STATE"
                            if destination in {"POSTGRESQL", "MYSQL"}
                            else "EVENT_LOG",
                            "connection",
                            "Initialize owned API scenario",
                            status="FAIL",
                            reason=str(error),
                        )
                    stack.diagnostics(evidence)
            if args.browser:
                from cdc_reliability_browser import browser_acceptance

                try:
                    browser_acceptance(stack, evidence, args.web_image)
                except Exception as error:
                    failure = True
                    evidence.add(
                        "browser-runner",
                        None,
                        None,
                        None,
                        "browser",
                        "Complete owned operator journey",
                        status="FAIL",
                        reason=str(error),
                    )
                    stack.diagnostics(evidence)
            if args.snapshot_interruption:
                scenario = Scenario(stack, evidence, "postgresql", "POSTGRESQL")
                scenario.table += "_snapshot"
                scenario.topic = scenario.table + ".public." + scenario.table
                try:
                    scenario.record(
                        "interrupted-snapshot-recovery",
                        "Snapshot is observably in progress before interruption; exact rows and streaming recover",
                        scenario.interrupt_snapshot,
                    )
                except Exception:
                    failure = True
                    stack.diagnostics(evidence)
                finally:
                    scenario.connect.close()
            if args.extra_sources:
                for source in args.sources:
                    try:
                        extra_source(stack, evidence, source)
                    except Exception as error:
                        failure = True
                        if not any(
                            r["test_id"] == source + ":extended"
                            for r in evidence.results
                        ):
                            evidence.add(
                                source + ":extended",
                                source,
                                "MINIO",
                                "EVENT_LOG",
                                "crud",
                                "Real source assertions",
                                status="FAIL",
                                reason=str(error),
                            )
                        stack.diagnostics(evidence)
            if args.source_history_expiration:
                scenario = Scenario(stack, evidence, "postgresql", "POSTGRESQL")
                scenario.table += "_history"
                scenario.topic = scenario.table + ".public." + scenario.table
                try:
                    scenario.record(
                        "expired-source-history",
                        "Expired WAL generates actionable recovery-required diagnostics and preserves saved positions",
                        scenario.expire_source_history,
                    )
                except Exception:
                    failure = True
                    stack.diagnostics(evidence)
                finally:
                    scenario.connect.close()
        except Exception as error:
            failure = True
            evidence.add(
                "infrastructure",
                None,
                None,
                None,
                "provision",
                "Healthy isolated fixtures",
                status="BLOCKED",
                reason=str(error),
            )
            stack.diagnostics(evidence)
        finally:
            try:
                stack.close()
            except Exception as error:
                failure = True
                evidence.add(
                    "cleanup",
                    None,
                    None,
                    None,
                    "cleanup",
                    "Stop all owned processes",
                    status="FAIL",
                    reason=str(error),
                )
    compatibility(evidence)
    evidence.write()
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
