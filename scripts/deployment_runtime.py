"""OPS-03: prove read-only production startup and graceful shutdown in isolation."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def docker(*args: str, environment: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Docker command failed ({args[0]}); output withheld")
    return result.stdout.strip()


def wait_ready(container: str, probe: list[str]) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        running = docker("inspect", "--format", "{{.State.Running}}", container)
        if running != "true":
            raise RuntimeError(
                f"Production fixture {container} stopped before readiness"
            )
        result = subprocess.run(
            ["docker", "exec", container, *probe],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"Production fixture {container} did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--web-image", required=True)
    args = parser.parse_args()
    prefix = "cluecdc-runtime-" + uuid.uuid4().hex[:12]
    network = prefix + "-network"
    containers: list[str] = []
    env = dict(os.environ)
    env.update(
        ENVIRONMENT="production",
        AUTH_MODE="session",
        BOOTSTRAP_DEFAULT_ADMIN="false",
        PUBLIC_URL="https://cluecdc.example.com",
        CORS_ORIGINS='["https://cluecdc.example.com"]',
        SESSION_SECRET=secrets.token_urlsafe(32),
        CONNECT_SECRET_TOKEN=secrets.token_urlsafe(32),
        SECRET_ENCRYPTION_KEY=base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode(),
        POSTGRES_PASSWORD=secrets.token_urlsafe(32),
        POSTGRES_DB="runtime",
        POSTGRES_USER="runtime",
    )
    database = prefix + "-postgres"
    env["DATABASE_URL"] = (
        f"postgresql+asyncpg://runtime:{env['POSTGRES_PASSWORD']}@{database}:5432/runtime"
    )
    app_variables = [
        "ENVIRONMENT",
        "AUTH_MODE",
        "BOOTSTRAP_DEFAULT_ADMIN",
        "PUBLIC_URL",
        "CORS_ORIGINS",
        "SESSION_SECRET",
        "CONNECT_SECRET_TOKEN",
        "SECRET_ENCRYPTION_KEY",
        "DATABASE_URL",
    ]
    app_env_args = [part for name in app_variables for part in ("-e", name)]
    runtime_args = [
        "--network",
        network,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "1000:1000",
        "--tmpfs",
        "/tmp:rw,nosuid,size=256m,uid=1000,gid=1000",
    ]
    docker("network", "create", network)
    try:
        containers.append(database)
        docker(
            "run",
            "-d",
            "--name",
            database,
            "--network",
            network,
            "-e",
            "POSTGRES_PASSWORD",
            "-e",
            "POSTGRES_DB",
            "-e",
            "POSTGRES_USER",
            "postgres:17.9",
            environment=env,
        )
        wait_ready(database, ["pg_isready", "-U", "runtime", "-d", "runtime"])
        migration = prefix + "-migration"
        containers.append(migration)
        docker(
            "run",
            "--name",
            migration,
            *runtime_args,
            *app_env_args,
            args.api_image,
            "alembic",
            "upgrade",
            "head",
            environment=env,
        )
        for component in ("api", "worker", "web"):
            name = prefix + "-" + component
            containers.append(name)
            extra = []
            variables = app_env_args
            image = args.api_image
            if component == "api":
                command = [
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                    "--timeout-graceful-shutdown",
                    "25",
                ]
                probe = [
                    "python",
                    "-c",
                    (
                        "import urllib.request; "
                        "urllib.request.urlopen('http://localhost:8000/health/ready', timeout=3)"
                    ),
                ]
                env["API_INTERNAL_URL"] = f"http://{name}:8000"
            elif component == "worker":
                command = ["python", "-m", "app.worker"]
                probe = ["python", "-m", "app.worker_health"]
            else:
                image = args.web_image
                variables = ["-e", "API_INTERNAL_URL"]
                extra = [
                    "--tmpfs",
                    "/app/apps/web/.next/cache:rw,nosuid,size=256m,uid=1000,gid=1000",
                ]
                command = ["node", "apps/web/server.js"]
                probe = [
                    "node",
                    "-e",
                    (
                        "fetch('http://localhost:3000').then(r=>{"
                        "if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
                    ),
                ]
            docker(
                "run",
                "-d",
                "--name",
                name,
                *runtime_args,
                *extra,
                *variables,
                image,
                *command,
                environment=env,
            )
            wait_ready(name, probe)
            started = time.monotonic()
            docker("stop", "--time", "30", name)
            code = docker("inspect", "--format", "{{.State.ExitCode}}", name)
            # Uvicorn preserves signal exit code 143 after its successful lifespan shutdown.
            if code not in {"0", "143"} or time.monotonic() - started >= 30:
                raise RuntimeError(
                    f"{component} failed graceful SIGTERM shutdown (exit {code})"
                )
            print(
                f"{component}: production readiness, read-only runtime and SIGTERM passed"
            )
        artifacts = ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)
        (artifacts / "deployment-runtime.json").write_text(
            json.dumps({"requirement": "OPS-03", "status": "PASS", "isolated": True})
            + "\n",
            encoding="utf-8",
        )
        return 0
    finally:
        # Every exact target was generated by this invocation; volumes contain test data only.
        for container in reversed(containers):
            subprocess.run(
                ["docker", "rm", "-f", "-v", container],
                capture_output=True,
                timeout=60,
                check=False,
            )
        subprocess.run(
            ["docker", "network", "rm", network],
            capture_output=True,
            timeout=60,
            check=False,
        )


if __name__ == "__main__":
    raise SystemExit(main())
