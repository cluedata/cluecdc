import json
import subprocess

import pytest
from scripts import kafka_security_acceptance as secure


def test_private_fixture_is_staged_over_stdin_without_host_bind_mount(monkeypatch, tmp_path):
    """SEC-03: Linux runner private directories must not be bind-mounted into other UIDs."""
    for name in secure.FIXTURE_FILES:
        (tmp_path / name).write_text("private-" + name)
    calls = []
    monkeypatch.setattr(secure, "docker", lambda *args, **kwargs: calls.append((args, kwargs)))
    secure.stage_fixture("owned-fixture", "api-image", tmp_path)
    arguments, options = calls[0]
    assert "type=volume,source=owned-fixture,target=/fixture" in arguments
    assert not any("type=bind" in value or "private-" in value for value in arguments)
    assert json.loads(options["stdin"]) == {
        name: "private-" + name for name in secure.FIXTURE_FILES
    }
    assert "0o750" in secure.STAGE_FILES and "0o640" in secure.STAGE_FILES


@pytest.mark.parametrize("oom", [False, True])
def test_exited_fixture_fails_before_transient_retry_loop(monkeypatch, oom):
    """TEST-01: a stopped broker cannot become ready; do not waste the full 120s timeout."""
    monkeypatch.setattr(
        secure,
        "docker",
        lambda *args: json.dumps({"Running": False, "ExitCode": 1, "OOMKilled": oom}),
    )
    monkeypatch.setattr(
        secure, "poll_docker", lambda *args: pytest.fail("must not exec stopped broker")
    )
    with pytest.raises(RuntimeError, match="stopped before readiness: exit=1") as failure:
        secure.readiness_probe("fixture", "probe")
    assert type(failure.value) is RuntimeError  # AcceptanceError would be retried by wait_for.


def test_running_fixture_is_still_polled_for_readiness(monkeypatch):
    monkeypatch.setattr(secure, "docker", lambda *args: json.dumps({"Running": True}))
    monkeypatch.setattr(secure, "poll_docker", lambda *args: args)
    assert secure.readiness_probe("fixture", "probe") == ("exec", "fixture", "probe")


def test_diagnostics_redact_raw_escaped_and_line_prefixed_private_material(tmp_path):
    """SEC-04: uploaded Java logs must not expose partial or escaped PEM values."""
    key = secure.certificates(tmp_path, "redaction.fixture")["ssl_key_pem"]
    private_line = key.splitlines()[1]
    raw = "fixture-password\n" + key + key.replace("\n", r"\n")
    raw += "\nlog prefix: " + private_line + "\npermission denied"
    sanitized = secure.redact_diagnostics(raw, ("fixture-password", key))
    assert "fixture-password" not in sanitized and private_line not in sanitized
    assert "permission denied" in sanitized


def test_diagnostic_timeout_does_not_skip_other_containers(monkeypatch):
    def run(command, **kwargs):
        if command[-1] == "stopped":
            raise subprocess.TimeoutExpired(command, 30)
        return subprocess.CompletedProcess(command, 0, "fixture-password", "")

    monkeypatch.setattr(secure.subprocess, "run", run)
    result = secure.collect_diagnostics(["stopped", "running"], ("fixture-password",))
    assert "unavailable" in result and "[REDACTED]" in result


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_cleanup_attempts_every_owned_resource_even_after_failure(monkeypatch, failure):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "connect":
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 60)
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(secure.subprocess, "run", run)
    assert not secure.cleanup_fixture(["broker", "connect"], "owned-volume")
    assert [command[-1] for command in calls] == ["connect", "broker", "owned-volume"]
