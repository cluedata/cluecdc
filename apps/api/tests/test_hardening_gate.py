import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


@pytest.mark.parametrize(
    ("scenario", "exit_code", "status", "cleanup"),
    [
        ("missing-docker", 2, "INFRASTRUCTURE_UNAVAILABLE", None),
        ("unavailable-daemon", 2, "INFRASTRUCTURE_UNAVAILABLE", None),
        ("probe-timeout", 2, "INFRASTRUCTURE_UNAVAILABLE", None),
        ("startup-failure", 1, "FAIL", "PASS"),
        ("startup-timeout", 1, "FAIL", "PASS"),
        ("diagnostic-timeout", 1, "FAIL", "PASS"),
        ("cleanup-timeout", 1, "FAIL", "FAIL"),
        ("existing-project", 1, "FAIL", None),
    ],
)
def test_gate_distinguishes_infrastructure_from_failures_and_preserves_unowned_resources(
    monkeypatch, tmp_path, scenario, exit_code, status, cleanup
):
    """TEST-01/SEC-04: unavailable tools are not passes; only owned fixtures get cleanup."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    gate = importlib.import_module("hardening_acceptance")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["hardening_acceptance.py"])
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(
        gate,
        "load_environment",
        lambda paths: {"AUTH_MODE": "developer", "TEST_PASSWORD": "fixture-private-password"},
    )
    ports = iter(range(30000, 30020))
    monkeypatch.setattr(gate, "available_port", lambda: next(ports))
    monkeypatch.setattr(
        gate.shutil, "which", lambda name: None if scenario == "missing-docker" else "/docker"
    )
    commands = []

    def output(command, **kwargs):
        if command[:2] == ["git", "rev-parse"]:
            return "fixture-commit"
        if command[0] == "docker" and scenario == "existing-project":
            return "unowned-resource"
        return ""

    def run(command, **kwargs):
        commands.append(command)
        if command == ["docker", "info"]:
            if scenario == "probe-timeout":
                raise subprocess.TimeoutExpired(command, 30)
            return subprocess.CompletedProcess(command, int(scenario == "unavailable-daemon"))
        if "up" in command:
            in_progress = json.loads((tmp_path / "artifacts" / "hardening-gate.json").read_text())
            assert in_progress["status"] == "RUNNING"
            assert in_progress["stages"][0]["status"] == "RUNNING"
            if scenario == "startup-timeout":
                raise subprocess.TimeoutExpired(command, 1800)
            return subprocess.CompletedProcess(command, 1)
        if "logs" in command:
            if scenario == "diagnostic-timeout":
                raise subprocess.TimeoutExpired(command, 60)
            return subprocess.CompletedProcess(command, 0, "fixture-private-password", "")
        if "down" in command:
            if scenario == "cleanup-timeout":
                raise subprocess.TimeoutExpired(command, 180)
            return subprocess.CompletedProcess(command, 0)
        pytest.fail("Unexpected command in isolated gate test")

    monkeypatch.setattr(gate.subprocess, "check_output", output)
    monkeypatch.setattr(gate.subprocess, "run", run)
    if cleanup == "FAIL":
        with pytest.raises(SystemExit) as failure:
            gate.main()
        assert failure.value.code == exit_code
    else:
        assert gate.main() == exit_code
    report = json.loads((tmp_path / "artifacts" / "hardening-gate.json").read_text())
    assert report["status"] == status
    assert report.get("cleanup") == cleanup
    assert report["git_sha"] == "fixture-commit"
    assert report["dirty"] is False
    teardown = [command for command in commands if "down" in command]
    assert bool(teardown) == (cleanup is not None)
    if teardown:
        assert teardown[0][teardown[0].index("-p") + 1] == report["project"]
        assert "--volumes" in teardown[0]
        diagnostic = (tmp_path / "artifacts" / "hardening-diagnostics.log").read_text()
        assert "fixture-private-password" not in diagnostic
        assert (
            "unavailable" in diagnostic
            if scenario == "diagnostic-timeout"
            else "[REDACTED]" in diagnostic
        )
        assert report["stages"][0]["status"] == "FAIL"
        if scenario == "startup-timeout":
            assert report["stages"][0]["failure_type"] == "TimeoutExpired"
    else:
        assert report["stages"] == []


def gate_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("hardening_acceptance")


def test_prebuilt_images_are_local_and_worker_reuses_exact_api_image(monkeypatch, tmp_path):
    """TEST-01: cached CI images change build cost, never the isolation or acceptance suite."""
    gate = gate_module(monkeypatch)
    commands = []
    monkeypatch.setattr(gate.subprocess, "run", lambda command, **kwargs: commands.append(command))
    compose = SimpleNamespace(command=["docker", "compose", "-p", "owned-project"])
    output = tmp_path / "images.json"
    gate.prepare_prebuilt_images(compose, "fixture", output)
    services = json.loads(output.read_text())["services"]
    assert set(services) == set(gate.PREBUILT_SERVICES)
    assert services["cluecdc-worker"] == services["cluecdc-api"] == {"image": "fixture-api:ci"}
    assert commands[0][:3] == ["docker", "image", "inspect"]
    assert len(commands[0][3:]) == 5
    assert compose.command[-2:] == ["-f", str(output)]


def test_missing_prebuilt_images_do_not_create_override_or_mutate_compose(monkeypatch, tmp_path):
    gate = gate_module(monkeypatch)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["docker", "image", "inspect"])

    monkeypatch.setattr(gate.subprocess, "run", fail)
    compose = SimpleNamespace(command=["docker", "compose"])
    output = tmp_path / "images.json"
    with pytest.raises(subprocess.CalledProcessError):
        gate.prepare_prebuilt_images(compose, "fixture", output)
    assert not output.exists() and compose.command == ["docker", "compose"]


def test_workflow_cache_tags_match_prebuilt_runner_contract(monkeypatch):
    """TEST-01: every loaded image must match the runner, with independent cache scopes."""
    gate = gate_module(monkeypatch)
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load((root / ".github/workflows/hardening.yml").read_text())
    steps = workflow["jobs"]["live-hardening"]["steps"]
    build = next(step for step in steps if step.get("uses", "").startswith("docker/bake-action@"))
    settings = dict(line.split("=", 1) for line in build["with"]["set"].splitlines())
    tags = {value for key, value in settings.items() if key.endswith(".tags")}
    assert tags == {
        f"cluecdc-hardening-ci-{suffix}:ci" for suffix in gate.PREBUILT_SERVICES.values()
    }
    assert build["with"]["load"] is True and not build["with"].get("push")
    caches = [value for key, value in settings.items() if key.endswith(".cache-to")]
    assert len(set(caches)) == 5
    assert all("ignore-error=true" in value and "mode=max" in value for value in caches)
    run = next(
        step["run"] for step in steps if "scripts/hardening_acceptance.py" in step.get("run", "")
    )
    assert "--prebuilt-image-prefix cluecdc-hardening-ci" in run and "--browser" in run
    upload = next(
        step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert "artifacts/kafka-security-diagnostics.log" in upload["with"]["path"]
