"""Safety and evidence contracts; these are unit tests, never live E2E evidence."""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def support(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("cdc_reliability_support")


def test_redaction_preserves_structure_and_removes_nested_secrets_and_uris(support):
    value = {
        "credentials": {"password": "private-value"},
        "offsets": {},
        "logs": "mongodb://user:private-value@host/db private-value",
        "nested": [{"authorization": "Bearer token"}],
    }
    result = support.redact(value, ["private-value", "{}"])
    assert result["offsets"] == {}
    assert result["credentials"] == "[REDACTED]"
    assert "private-value" not in json.dumps(result)
    assert "user:" not in result["logs"]
    assert result["nested"][0]["authorization"] == "[REDACTED]"


@pytest.mark.parametrize(
    "labels",
    [
        {
            "io.cluecdc.reliability.owner": "other",
            "com.docker.compose.project": "cluecdc-reliability-run",
        },
        {"io.cluecdc.reliability.owner": "run", "com.docker.compose.project": "production"},
        {},
    ],
)
def test_fault_injection_rejects_unowned_container_before_mutation(support, labels):
    stack = support.DisposableStack.__new__(support.DisposableStack)
    stack.run_id, stack.project = "run", "cluecdc-reliability-run"
    stack.compose = Mock(return_value="container")
    stack.command = Mock(return_value=json.dumps([{"Config": {"Labels": labels}}]))
    with pytest.raises(support.AcceptanceError, match="unowned"):
        stack.fault("kafka", "stop")
    assert stack.command.call_count == 1
    assert stack.command.call_args.args[0][:2] == ["docker", "inspect"]


def test_duplicate_measurement_never_implies_exactly_once(support):
    row = {"_clue_topic": "owned", "_clue_partition": "0", "_clue_offset": "5"}
    counts = support.history_identity([row, row])
    assert counts["unique_events"] == 1 and counts["duplicate_occurrences"] == 1
    assert "at-least-once" in counts["guarantee"]


def test_owned_fixture_auth_does_not_forward_an_inherited_operator_token(support, monkeypatch):
    monkeypatch.setenv("CLUECDC_TOKEN", "private-operator-token")
    client = support.ApiClient("http://owned-fixture", use_environment_token=False)
    client.request = Mock()
    client.authenticate(
        {"DEFAULT_ADMIN_EMAIL": "fixture@example.test", "DEFAULT_ADMIN_PASSWORD": "fixture-only"}
    )
    client.request.assert_called_once()
    assert client.request.call_args.args[0] == "/auth/login"


def test_unexecuted_matrix_does_not_promote_unit_tests_to_live_success(support, tmp_path):
    runner = importlib.import_module("cdc_reliability")
    evidence = SimpleNamespace(results=[], output=tmp_path)
    runner.compatibility(evidence)
    rows = json.loads((tmp_path / "compatibility-matrix.json").read_text())
    assert not any(r["e2e_status"] == "PASS" for r in rows)
    assert (
        next(r for r in rows if r["source"] == "mongodb" and r["destination"] == "POSTGRESQL")[
            "e2e_status"
        ]
        == "NOT SUPPORTED"
    )
    evidence.results = [
        {
            "source_type": "mongodb",
            "destination_type": "MINIO",
            "delivery_mode": "EVENT_LOG",
            "scenario": "crud",
            "status": "PASS",
            "observed_behavior": {
                "snapshot": "PASS",
                "outage_recovery": "PASS_WITH_EXISTING_TASK_RESTART",
            },
        }
    ]
    runner.compatibility(evidence)
    rows = json.loads((tmp_path / "compatibility-matrix.json").read_text())
    row = next(r for r in rows if r["source"] == "mongodb" and r["destination"] == "MINIO")
    assert row["capabilities"]["snapshot"] == "PASS"
    assert row["capabilities"]["recovery"] == "PASS"
    assert row["capabilities"]["connection"] == "PARTIAL"
    assert row["capabilities"]["replay"] == "NOT RUN"
    assert "explicit task restart" in row["verification_scope"]
    assert (
        next(
            r
            for r in rows
            if r["source"] == "postgresql"
            and r["destination"] == "CLICKHOUSE"
            and r["write_mode"] == "upsert"
        )["e2e_status"]
        == "NOT SUPPORTED"
    )
