import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("scenario", "exit_code", "status", "cleanup"),
    [
        ("missing-docker", 2, "INFRASTRUCTURE_UNAVAILABLE", None),
        ("unavailable-daemon", 2, "INFRASTRUCTURE_UNAVAILABLE", None),
        ("startup-failure", 1, "FAIL", "PASS"),
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
            return subprocess.CompletedProcess(command, int(scenario == "unavailable-daemon"))
        if "up" in command:
            return subprocess.CompletedProcess(command, 1)
        if "logs" in command:
            return subprocess.CompletedProcess(command, 0, "fixture-private-password", "")
        if "down" in command:
            return subprocess.CompletedProcess(command, 0)
        pytest.fail("Unexpected command in isolated gate test")

    monkeypatch.setattr(gate.subprocess, "check_output", output)
    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.main() == exit_code
    report = json.loads((tmp_path / "artifacts" / "hardening-gate.json").read_text())
    assert report["status"] == status
    assert report.get("cleanup") == cleanup
    assert report["git_sha"] == "fixture-commit"
    assert report["dirty"] is False
    teardown = [command for command in commands if "down" in command]
    assert bool(teardown) == (cleanup == "PASS")
    if teardown:
        assert teardown[0][teardown[0].index("-p") + 1] == report["project"]
        assert "--volumes" in teardown[0]
        diagnostic = (tmp_path / "artifacts" / "hardening-diagnostics.log").read_text()
        assert "fixture-private-password" not in diagnostic
        assert "[REDACTED]" in diagnostic
        assert report["stages"][0]["status"] == "FAIL"
    else:
        assert report["stages"] == []
