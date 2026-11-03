"""Owned disposable services and evidence for the existing CDC acceptance suite."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cdc_acceptance_support import AcceptanceError, ApiClient, wait_for

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_IMAGES = json.loads(
    (Path(__file__).parent / "cdc_reliability_images.json").read_text()
)
STATUSES = {"PASS", "FAIL", "PARTIAL", "BLOCKED", "NOT RUN", "NOT SUPPORTED"}


def fixture_port():
    # An explicit ephemeral-range port stays stable across container stop/start.
    # Docker published=0 may allocate a different port after every restart.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return str(listener.getsockname()[1])


def redact(value, secrets=()):
    def clean(item):
        if isinstance(item, dict):
            return {
                k: "[REDACTED]"
                if re.search(
                    r"(?i)password|secret|token|private.?key|authorization|credentials",
                    k,
                )
                else clean(v)
                for k, v in item.items()
            }
        if isinstance(item, list):
            return [clean(v) for v in item]
        if isinstance(item, str):
            for secret in sorted(
                (s for s in secrets if len(s) >= 4), key=len, reverse=True
            ):
                item = item.replace(secret, "[REDACTED]")
            item = re.sub(
                r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", item
            )
            item = re.sub(
                r"""(?i)((?:password|passwd|secret|access[_ .-]?key|token)[\w.-]*["']?\s*[:=]\s*)["']?[^\s,;"']+""",
                r"\1[REDACTED]",
                item,
            )
            return re.sub(
                r"(?i)([a-z][a-z0-9+.-]*://)[^\s/\"@]+:[^\s/\"@]+@",
                r"\1[REDACTED]@",
                item,
            )
        return item

    return clean(json.loads(json.dumps(value, default=str)))


class Evidence:
    def __init__(self, output: Path, environment: str, secrets=()):
        self.output, self.environment, self.secrets = output, environment, secrets
        self.results = []
        self.commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        self.versions = {}

    def add(
        self,
        test_id,
        source,
        destination,
        mode,
        scenario,
        expected,
        *,
        status="PASS",
        observed=None,
        duration=0,
        reason=None,
        offsets=None,
        tier="real E2E",
        flush=True,
    ):
        if status not in STATUSES:
            raise ValueError(status)
        self.results.append(
            redact(
                {
                    "test_id": test_id,
                    "source_type": source,
                    "destination_type": destination,
                    "delivery_mode": mode,
                    "versions": self.versions,
                    "commit_sha": self.commit,
                    "working_tree": "implementation under test; see patch and regression report",
                    "test_environment": self.environment,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "scenario": scenario,
                    "expected_behavior": expected,
                    "observed_behavior": observed,
                    "status": status,
                    "duration_seconds": round(duration, 3),
                    "offset_evidence": offsets,
                    "failure_reason": reason,
                    "artifact_path": str(self.output / "summary.json"),
                    "verification_tier": tier,
                },
                self.secrets,
            )
        )
        if flush:
            self.write()

    def write(self):
        self.output.mkdir(parents=True, exist_ok=True)
        summary = {
            "commit_sha": self.commit,
            "environment": self.environment,
            "counts": dict(Counter(r["status"] for r in self.results)),
            "results": self.results,
        }
        summary_path = self.output / "summary.json"
        temporary = self.output / "summary.json.tmp"
        temporary.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary.replace(summary_path)
        for name, predicate in {
            "data-correctness": lambda r: (
                r["scenario"]
                in {"snapshot", "crud", "event-identity", "final-data-correctness"}
                or r["scenario"].startswith("interrupted-snapshot")
            ),
            "recovery-tests": lambda r: (
                "recovery" in r["scenario"]
                or "outage" in r["scenario"]
                or r["scenario"] in {"expired-source-history", "replay"}
            ),
            "checkpoint-tests": lambda r: "checkpoint" in r["scenario"],
            "regression-results": lambda r: r["verification_tier"] == "regression",
        }.items():
            (self.output / (name + ".json")).write_text(
                json.dumps(
                    [r for r in self.results if predicate(r)],
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )


class DisposableStack:
    """Generate an isolated copy of existing Compose fixtures; never attach shared services."""

    def __init__(self, api_image, connect_image, minio_image, *, retain=False):
        self.run_id = uuid.uuid4().hex[:12]
        self.project = "cluecdc-reliability-" + self.run_id
        self.retain = retain
        self.directory = Path(tempfile.mkdtemp(prefix=self.project + "-"))
        self.path = self.directory / "compose.json"
        self.env = dict(os.environ)
        self.defaults = {}
        # Deliberately never read .env or inherit its production/service overrides.
        for filename in (".env.example", ".env.test.example"):
            for line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    self.defaults[key] = value
        # The generic integration example uses developer mode. Reliability must
        # exercise actual sign-in, sessions and RBAC, including the browser suite.
        self.defaults["AUTH_MODE"] = "session"
        self.env.update(self.defaults)
        rendered = self.command(
            [
                "docker",
                "compose",
                "--env-file",
                str(ROOT / ".env.example"),
                "--env-file",
                str(ROOT / ".env.test.example"),
                "-f",
                "compose.yaml",
                "-f",
                "compose.test.yaml",
                "config",
                "--format",
                "json",
            ]
        )
        config = json.loads(rendered)
        config["name"] = self.project
        for name in ("cluecdc-web", "commerce-generator"):
            config["services"].pop(name, None)
        images = {
            "cluecdc-api": api_image,
            "cluecdc-worker": api_image,
            "minio-bootstrap": api_image,
            "kafka-connect": connect_image,
            "minio": minio_image,
        }
        for name, reference in images.items():
            inspected = subprocess.run(
                ["docker", "image", "inspect", reference, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if inspected.returncode == 0:
                images[name] = inspected.stdout.strip()
        for name, service in config["services"].items():
            service.pop("build", None)
            service.pop("container_name", None)
            service["restart"] = "no"
            service["labels"] = {"io.cluecdc.reliability.owner": self.run_id}
            if name in images:
                service["image"] = images[name]
            else:
                service["image"] = FIXTURE_IMAGES.get(
                    service["image"], service["image"]
                )
            for port in service.get("ports", []):
                port["published"] = fixture_port()
                port["host_ip"] = "127.0.0.1"
        # Test-only checkpoint cadence: the MinIO sink publishes pending objects
        # at Connect commits. Keep real commits while avoiding a minute per CRUD.
        config["services"]["kafka-connect"]["environment"][
            "OFFSET_FLUSH_INTERVAL_MS"
        ] = "1000"
        external = next(
            p["published"]
            for p in config["services"]["kafka"]["ports"]
            if p["target"] == 9092
        )
        config["services"]["kafka"]["environment"]["KAFKA_ADVERTISED_LISTENERS"] = (
            "INTERNAL://kafka:29092,EXTERNAL://127.0.0.1:" + external
        )
        config["services"]["clickhouse"] = {
            "image": FIXTURE_IMAGES["clickhouse/clickhouse-server:26.8.20.9-alpine"],
            "environment": {
                "CLICKHOUSE_USER": "cdc_test",
                "CLICKHOUSE_PASSWORD": "isolated-cdc-only",
                "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT": "1",
            },
            "ports": [
                {"target": 8123, "published": fixture_port(), "host_ip": "127.0.0.1"}
            ],
            "labels": {"io.cluecdc.reliability.owner": self.run_id},
            "healthcheck": {
                "test": ["CMD", "wget", "-qO-", "http://127.0.0.1:8123/ping"],
                "interval": "5s",
                "timeout": "3s",
                "retries": 30,
            },
        }
        for category in ("networks", "volumes"):
            for name, definition in config.get(category, {}).items():
                definition["name"] = self.project + "_" + name
                definition["labels"] = {"io.cluecdc.reliability.owner": self.run_id}
                if definition.get("external"):
                    raise AcceptanceError(
                        "Reliability fixtures cannot use external resources"
                    )
        self.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    def command(self, argv, *, stdin=None, timeout=180):
        result = subprocess.run(
            argv,
            cwd=ROOT,
            env=self.env,
            input=stdin,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode:
            sensitive = [
                v
                for k, v in self.defaults.items()
                if re.search("PASSWORD|SECRET|TOKEN|KEY", k)
            ]
            sensitive += [
                "local-cdc-fixture-password",
                "Local-CDC-Fixture-Password-123!",
            ]
            self.last_command_error = redact(
                {
                    "operation": argv[:2],
                    "exit_code": result.returncode,
                    "stderr": result.stderr[-10000:],
                },
                sensitive,
            )
            raise AcceptanceError(
                f"Fixture {argv[0]} {argv[1]} failed; sanitized diagnostic collected"
            )
        return result.stdout.strip()

    def compose(self, *arguments, timeout=180):
        return self.command(
            ["docker", "compose", "-p", self.project, "-f", str(self.path), *arguments],
            timeout=timeout,
        )

    def start(self):
        self.compose("up", "-d", "--wait", "--wait-timeout", "240", timeout=300)

    def add_source(self, source):
        if source not in {"mariadb", "sqlserver", "mongodb"}:
            raise AcceptanceError("Unsupported optional source fixture")
        config = json.loads(self.path.read_text(encoding="utf-8"))
        optional = json.loads(
            self.command(
                [
                    "docker",
                    "compose",
                    "-f",
                    "compose.sources.yaml",
                    "--profile",
                    "sources-" + source,
                    "config",
                    "--format",
                    "json",
                ]
            )
        )
        service = optional["services"][source + "-source"]
        service["image"] = FIXTURE_IMAGES[service["image"]]
        service.pop("profiles", None)
        service["labels"] = {"io.cluecdc.reliability.owner": self.run_id}
        for port in service.get("ports", []):
            port["published"] = fixture_port()
            port["host_ip"] = "127.0.0.1"
        config["services"][source + "-source"] = service
        self.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        self.compose(
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "180",
            source + "-source",
            timeout=210,
        )

    def add_web(self, image):
        config = json.loads(self.path.read_text(encoding="utf-8"))
        port = fixture_port()
        public_url = "http://127.0.0.1:" + port
        self.defaults["PUBLIC_URL"] = public_url
        self.env["PUBLIC_URL"] = public_url
        for service in ("cluecdc-api", "cluecdc-worker"):
            config["services"][service]["environment"]["PUBLIC_URL"] = public_url
        config["services"]["cluecdc-web"] = {
            "image": image,
            "environment": {"API_INTERNAL_URL": "http://cluecdc-api:8000"},
            "ports": [{"target": 3000, "published": port, "host_ip": "127.0.0.1"}],
            "labels": {"io.cluecdc.reliability.owner": self.run_id},
        }
        self.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        self.compose(
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "120",
            "cluecdc-api",
            "cluecdc-worker",
            "cluecdc-web",
            timeout=180,
        )

    def url(self, service, port):
        return "http://" + self.compose("port", service, str(port))

    def owned_container(self, service):
        container = self.compose("ps", "-a", "-q", service)
        if not container:
            raise AcceptanceError("Missing owned fixture container")
        info = json.loads(self.command(["docker", "inspect", container]))[0]
        labels = info["Config"]["Labels"]
        if (
            labels.get("io.cluecdc.reliability.owner") != self.run_id
            or labels.get("com.docker.compose.project") != self.project
        ):
            raise AcceptanceError(
                "Refusing fault injection against an unowned container"
            )
        return container

    def fault(self, service, action):
        if action not in {"stop", "start", "restart"}:
            raise AcceptanceError("Unsupported fixture fault action")
        self.command(["docker", action, self.owned_container(service)], timeout=120)

    def wait_service(self, service):
        def ready():
            info = json.loads(
                self.command(["docker", "inspect", self.owned_container(service)])
            )[0]
            state = info["State"]
            return (
                state.get("Running")
                and state.get("Health", {}).get("Status", "healthy") == "healthy"
            )

        return wait_value(ready, bool, "restored fixture " + service, timeout=180)

    def exec(self, service, command, stdin=None, timeout=90):
        return self.command(
            ["docker", "exec", "-i", self.owned_container(service), *command],
            stdin=stdin,
            timeout=timeout,
        )

    def sql(self, source, statement, database="cluecdc_source"):
        if source == "postgresql":
            return self.exec(
                "postgres-demo",
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
                statement,
            )
        return self.exec(
            "mysql-demo",
            [
                "sh",
                "-ec",
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --default-character-set=utf8mb4 -N -B "$0"',
                database,
            ],
            statement,
        )

    def kafka_events(self, topic):
        # Reading from inside the API fixture avoids external advertised listeners
        # and never commits a group offset or changes a connector's state.
        script = """import json, time, uuid
from confluent_kafka import Consumer, TopicPartition
c=Consumer({"bootstrap.servers":"kafka:29092","group.id":"verify-"+uuid.uuid4().hex,
"enable.auto.commit":False,"enable.auto.offset.store":False,"allow.auto.create.topics":False})
try:
 topic=TOPIC
 md=c.list_topics(topic,timeout=5).topics.get(topic)
 rows=[]
 if md and not md.error:
  c.assign([TopicPartition(topic,p,0) for p in md.partitions])
  end=time.monotonic()+2
  while time.monotonic()<end and len(rows)<10000:
   m=c.poll(.1)
   if m is None: continue
   if m.error(): raise RuntimeError("Kafka read failed")
   v=json.loads(m.value()) if m.value() else None
   k=json.loads(m.key()) if m.key() else None
   rows.append({"topic":m.topic(),"partition":m.partition(),"offset":m.offset(),
    "raw_key":m.key().decode() if m.key() else None,
    "raw_value":m.value().decode() if m.value() else None,
    "key":k.get("payload",k) if isinstance(k,dict) else k,
    "value":v.get("payload",v) if isinstance(v,dict) else v})
 print(json.dumps(rows,ensure_ascii=False))
finally: c.close()
""".replace("TOPIC", repr(topic))
        return json.loads(
            self.exec("cluecdc-api", ["python", "-c", script], timeout=20)
        )

    def replay(self, topic, records):
        if not topic.startswith("cdc_" + self.run_id + "_"):
            raise AcceptanceError("Replay is restricted to this run's owned topic")
        script = """import json,sys
from confluent_kafka import Producer
p=Producer({"bootstrap.servers":"kafka:29092","enable.idempotence":True})
data=json.load(sys.stdin)
for r in data["records"]:
 p.produce(data["topic"],key=r["raw_key"],value=r["raw_value"],partition=r["partition"])
if p.flush(20): raise RuntimeError("Replay did not flush")
"""
        self.exec(
            "cluecdc-api",
            ["python", "-c", script],
            json.dumps({"topic": topic, "records": records}),
            timeout=30,
        )

    def diagnostics(self, evidence):
        try:
            logs = self.compose("logs", "--no-color", "--tail", "60", timeout=30)
            path = evidence.output / "failures"
            path.mkdir(parents=True, exist_ok=True)
            (path / (self.project + ".json")).write_text(
                json.dumps(
                    redact(
                        {
                            "logs": logs,
                            "last_command_error": getattr(
                                self, "last_command_error", None
                            ),
                        },
                        evidence.secrets,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    def close(self):
        if not self.retain:
            # No --volumes: durable failure resources remain available for inspection.
            self.compose("down", "--remove-orphans", timeout=180)


def history_identity(rows):
    counts = Counter(
        (r["_clue_topic"], int(r["_clue_partition"]), int(r["_clue_offset"]))
        for r in rows
    )
    return {
        "rows": len(rows),
        "unique_events": len(counts),
        "duplicate_occurrences": sum(n - 1 for n in counts.values()),
        "guarantee": "at-least-once; duplicates measured, not forbidden",
    }


def wait_value(fn, predicate, label, timeout=180):
    return wait_for(fn, predicate, label, seconds=timeout)


def api_for(stack):
    if getattr(stack, "_api_client", None) is not None:
        return stack._api_client
    sensitive = [
        v
        for k, v in stack.defaults.items()
        if re.search("PASSWORD|SECRET|TOKEN|KEY", k) and len(v) >= 4
    ]
    api = ApiClient(
        stack.url("cluecdc-api", 8000) + "/api/v1",
        sensitive,
        use_environment_token=False,
    )
    api.authenticate(stack.defaults)
    stack._api_client = api
    return api
