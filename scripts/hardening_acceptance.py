"""TEST-01: own a disposable stack and execute the mandatory live hardening gate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cdc_acceptance_support import ROOT, Compose, load_environment


def available_port() -> int:
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return int(connection.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", action="append", type=Path)
    parser.add_argument(
        "--browser", action="store_true", help="Also run demo fixtures and Playwright"
    )
    args = parser.parse_args()
    env_files = args.env_file or [ROOT / ".env.example", ROOT / ".env.test.example"]
    environment = load_environment(env_files)
    if environment.get("AUTH_MODE") != "developer":
        parser.error(
            "Disposable acceptance requires AUTH_MODE=developer in test configuration"
        )
    if args.browser and (ROOT / ".env").resolve() not in [
        path.resolve() for path in env_files
    ]:
        parser.error(
            "Browser acceptance requires the CI .env fixture also passed with --env-file"
        )
    project = "cluecdc-hardening-" + uuid.uuid4().hex[:12]
    ports: dict[str, int] = {}
    for name in (
        "CLUECDC_WEB_PORT",
        "CLUECDC_API_PORT",
        "KAFKA_CONNECT_PORT",
        "KAFKA_PORT",
        "POSTGRES_SOURCE_PORT",
        "POSTGRES_DESTINATION_PORT",
        "MYSQL_SOURCE_PORT",
        "MYSQL_DESTINATION_PORT",
        "MINIO_PORT",
    ):
        port = available_port()
        while port in ports.values():
            port = available_port()
        ports[name] = port
    process_environment = dict(os.environ)
    process_environment.update(environment)
    process_environment.update({key: str(value) for key, value in ports.items()})
    process_environment.update(
        COMPOSE_PROJECT_NAME=project,
        KAFKA_TEST_BOOTSTRAP=f"localhost:{ports['KAFKA_PORT']}",
        PLAYWRIGHT_BASE_URL=f"http://localhost:{ports['CLUECDC_WEB_PORT']}",
    )
    os.environ.update(process_environment)
    compose = Compose(project, env_files)
    api_url = f"http://localhost:{ports['CLUECDC_WEB_PORT']}/api/v1"
    health_url = f"http://localhost:{ports['CLUECDC_API_PORT']}/health/ready"
    connect_url = f"http://localhost:{ports['KAFKA_CONNECT_PORT']}"
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    report = {
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "started_at": datetime.now(UTC).isoformat(),
        "dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            ).strip()
        ),
        "browser_requested": args.browser,
        "project": project,
        "status": "RUNNING",
        "stages": [],
    }

    def stage(category: str, command: list[str], timeout: int = 1200) -> None:
        print(f"Running {category}", flush=True)
        started = time.monotonic()
        entry = {"category": category, "status": "RUNNING"}
        report["stages"].append(entry)
        result = subprocess.run(command, cwd=ROOT, check=False, timeout=timeout)
        entry.update(
            status="PASS" if result.returncode == 0 else "FAIL",
            seconds=round(time.monotonic() - started, 1),
        )
        if result.returncode:
            raise RuntimeError(f"{category} failed with exit code {result.returncode}")

    def script(name: str, *extra: str, category: str | None = None) -> None:
        command = [
            sys.executable,
            str(ROOT / "scripts" / name),
            "--compose-project",
            project,
            "--api-url",
            api_url,
        ]
        for path in env_files:
            command.extend(["--env-file", str(path)])
        stage(category or name, [*command, *extra])

    ready_to_cleanup = False
    try:
        if shutil.which("docker") is None:
            report["status"] = "INFRASTRUCTURE_UNAVAILABLE"
            return 2
        probe = subprocess.run(
            ["docker", "info"], capture_output=True, check=False, timeout=30
        )
        if probe.returncode:
            report["status"] = "INFRASTRUCTURE_UNAVAILABLE"
            return 2
        # The generated project must have no pre-existing resources before ownership is claimed.
        for resource in ("ps", "volume", "network"):
            command = (
                ["docker", resource, "ls", "-q"]
                if resource != "ps"
                else ["docker", "ps", "-aq"]
            )
            existing = subprocess.check_output(
                [*command, "--filter", f"label=com.docker.compose.project={project}"],
                text=True,
            ).strip()
            if existing:
                raise RuntimeError("Refusing to use an existing Compose project")
        ready_to_cleanup = True
        stage(
            "integration-startup",
            [
                *compose.command,
                "up",
                "-d",
                "--build",
                "--wait",
                "--wait-timeout",
                "300",
            ],
            timeout=1800,
        )
        script("cdc-integrity.py", category="CDC-01/integrity")
        script(
            "cdc-recovery.py",
            "--api-health-url",
            health_url,
            "--connect-url",
            connect_url,
            category="CDC-02/connector-recovery",
        )
        script("cdc-schema-safety.py", category="CDC-03/schema-snapshots")
        script("cdc-retention-safety.py", category="CDC-04/retention")
        script(
            "cdc-failure-recovery.py",
            "--api-health-url",
            health_url,
            "--connect-url",
            connect_url,
            category="REL-01/failure-injection",
        )
        script(
            "demo-object-storage.py",
            "--minio-url",
            f"http://localhost:{ports['MINIO_PORT']}",
            category="CDC-01/object-storage",
        )
        script(
            "verify-secrets.py",
            "--connect-url",
            connect_url,
            category="SEC-04/secret-boundaries",
        )
        metadata = [
            sys.executable,
            str(ROOT / "scripts/metadata_acceptance.py"),
            "--compose-project",
            project,
        ]
        for path in env_files:
            metadata.extend(["--env-file", str(path)])
        stage("OPS-01+OPS-02/backup-restore-upgrade", metadata)
        api_id = compose.run("ps", "-q", "cluecdc-api")
        web_id = compose.run("ps", "-q", "cluecdc-web")
        images = [
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{.Image}}", item], text=True
            ).strip()
            for item in (api_id, web_id)
        ]
        stage(
            "OPS-03/production-runtime",
            [
                sys.executable,
                str(ROOT / "scripts/deployment_runtime.py"),
                "--api-image",
                images[0],
                "--web-image",
                images[1],
            ],
        )
        # Run browser checks before the secure fixture adds a second independent Connect endpoint.
        if args.browser:
            for name, extra in (
                ("demo.py", []),
                ("demo-destination.py", ["--skip-capture-demo"]),
            ):
                stage(
                    "browser-fixture/" + name,
                    [
                        sys.executable,
                        str(ROOT / "scripts" / name),
                        "--api-url",
                        api_url,
                        *extra,
                    ],
                )
            stage(
                "browser-e2e",
                [shutil.which("npm") or "npm", "run", "test:e2e", "-w", "@cluecdc/web"],
            )
        script("kafka_security_acceptance.py", category="SEC-03/TLS-SASL-source-sink")
        report["status"] = "PASS"
        return 0
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        report["status"] = "FAIL"
        # Do not persist exception/command output that could contain provider credentials.
        report["failure_type"] = type(error).__name__
        print(
            f"Hardening gate failed ({type(error).__name__}); inspect the stage report",
            flush=True,
        )
        return 1
    finally:
        if ready_to_cleanup:
            # Logs may contain test fixture configuration. Persist only sanitized diagnostics.
            logs = subprocess.run(
                [*compose.command, "logs", "--no-color", "--tail", "100"],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            diagnostic = logs.stdout + logs.stderr
            for key, value in environment.items():
                if value and any(
                    token in key
                    for token in ("PASSWORD", "SECRET", "TOKEN", "ACCESS_KEY")
                ):
                    diagnostic = diagnostic.replace(value, "[REDACTED]")
            (artifacts / "hardening-diagnostics.log").write_text(
                diagnostic, encoding="utf-8"
            )
            cleanup = subprocess.run(
                [*compose.command, "down", "--volumes", "--remove-orphans"],
                capture_output=True,
                check=False,
                timeout=180,
            )
            report["cleanup"] = "PASS" if cleanup.returncode == 0 else "FAIL"
            if cleanup.returncode:
                report["status"] = "FAIL"
        report["finished_at"] = datetime.now(UTC).isoformat()
        (artifacts / "hardening-gate.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"Hardening gate: {report['status']}; cleanup: {report.get('cleanup', 'not started')}",
            flush=True,
        )
        if report.get("cleanup") == "FAIL":
            raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
