"""Run existing operator/browser acceptance against only an owned fixture stack."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from cdc_reliability_support import ROOT, redact, wait_value


def browser_acceptance(stack, evidence, image):
    stack.add_web(image)
    base = stack.url("cluecdc-web", 3000)
    wait_value(
        lambda: httpx.get(base + "/login", timeout=10).status_code,
        lambda s: s == 200,
        "isolated browser application",
    )
    for service in ("postgres-demo", "mysql-demo", "kafka", "cluecdc-api"):
        stack.owned_container(service)
    environment = {
        **stack.env,
        "COMPOSE_PROJECT_NAME": stack.project,
        "PLAYWRIGHT_BASE_URL": base,
        "KAFKA_TEST_BOOTSTRAP": stack.url("kafka", 9092).removeprefix("http://"),
        "CLUECDC_UI_EMAIL": stack.defaults["DEFAULT_ADMIN_EMAIL"],
        "CLUECDC_UI_PASSWORD": stack.defaults["DEFAULT_ADMIN_PASSWORD"],
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        "PYTHONIOENCODING": "utf-8",
    }
    environment.pop("CLUECDC_TOKEN", None)
    # Prove that legacy scripts' Compose commands resolve this project before
    # allowing their SQL fixtures to execute. They never target the shared stack.
    resolved = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.test.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    assert json.loads(resolved.stdout)["name"] == stack.project
    protected = [
        ROOT / "artifacts" / name
        for name in (
            "demo-result.json",
            "destination-demo-result.json",
            "cdc-effective-key.json",
        )
    ]
    backups = {p: p.read_bytes() if p.exists() else None for p in protected}

    def execute(test_id, command, timeout=600):
        started = time.monotonic()
        group = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            **group,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stdout, stderr = process.communicate(timeout=15)
        result = subprocess.CompletedProcess(
            command, 124 if timed_out else process.returncode, stdout, stderr
        )
        log = evidence.output / (test_id + ".json")
        log.write_text(
            json.dumps(
                redact(
                    {"stdout": result.stdout, "stderr": result.stderr}, evidence.secrets
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        evidence.add(
            test_id,
            "postgresql/mysql",
            "POSTGRESQL/MYSQL/MINIO",
            "operator workflow",
            "browser"
            if test_id == "playwright"
            else "effective-key"
            if test_id == "effective-key"
            else "api-demo",
            "Existing acceptance script assertions on isolated real services",
            status="PASS" if result.returncode == 0 else "FAIL",
            observed={"exit_code": result.returncode, "log_path": str(log)},
            reason=None if result.returncode == 0 else "See sanitized runner output",
            duration=time.monotonic() - started,
        )
        if result.returncode:
            raise RuntimeError(
                "Browser acceptance prerequisite/test failed: " + test_id
            )

    try:
        api = stack.url("cluecdc-api", 8000) + "/api/v1"
        execute("capture-demo", [sys.executable, "scripts/demo.py", "--api-url", api])
        execute(
            "jdbc-demo",
            [
                sys.executable,
                "scripts/demo-destination.py",
                "--api-url",
                api,
                "--skip-capture-demo",
            ],
        )
        execute(
            "effective-key",
            [
                sys.executable,
                "scripts/cdc-effective-key.py",
                "--api-url",
                api,
                "--compose-project",
                stack.project,
                "--env-file",
                ".env.example",
                "--env-file",
                ".env.test.example",
            ],
        )
        # Copy this run's key evidence before restoring any pre-existing artifact.
        (evidence.output / "effective-key-evidence.json").write_bytes(
            protected[2].read_bytes()
        )
        config = ROOT / ".codex-tmp" / (stack.project + ".playwright.config.ts")
        config.parent.mkdir(exist_ok=True)
        config.write_text(
            "import base from "
            + json.dumps((ROOT / "apps/web/playwright.config.ts").as_posix())
            + ";\n"
            "export default {...base, testDir: "
            + json.dumps((ROOT / "apps/web/tests/e2e").as_posix())
            + ", "
            "outputDir: "
            + json.dumps((evidence.output.resolve() / "browser-results").as_posix())
            + ", "
            "use: {...base.use, screenshot: 'off'}};\n",
            encoding="utf-8",
        )
        try:
            execute(
                "playwright",
                [
                    shutil.which("npm.cmd" if os.name == "nt" else "npm"),
                    "run",
                    "test:e2e",
                    "-w",
                    "@cluecdc/web",
                    "--",
                    "--config",
                    str(config),
                ],
                timeout=1200,
            )
        finally:
            config.unlink(missing_ok=True)
    finally:
        for path, contents in backups.items():
            if contents is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(contents)
