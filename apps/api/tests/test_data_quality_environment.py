"""Reject unowned database fixtures before opening a database connection."""

import json
from unittest.mock import Mock

import pytest
from scripts import data_quality_environment as fixture


def test_operator_project_is_rejected_before_docker(monkeypatch):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "cluecdc")
    run = Mock()
    monkeypatch.setattr(fixture.subprocess, "check_output", run)
    with pytest.raises(RuntimeError, match="disposable"):
        fixture.compose("ps", "-q", "metadata-db")
    run.assert_not_called()


@pytest.mark.parametrize(
    "project,host", [("production", "127.0.0.1"), ("cluecdc-test-owned", "0.0.0.0")]
)
def test_wrong_owner_or_public_binding_is_rejected(monkeypatch, project, host):
    fixture.port.cache_clear()
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "cluecdc-test-owned")
    monkeypatch.setattr(fixture, "compose", Mock(return_value="container-id"))
    monkeypatch.setattr(
        fixture.subprocess,
        "check_output",
        Mock(
            return_value=json.dumps(
                [
                    {
                        "Config": {"Labels": {"com.docker.compose.project": project}},
                        "NetworkSettings": {
                            "Ports": {"5432/tcp": [{"HostIp": host, "HostPort": "5433"}]}
                        },
                    }
                ]
            )
        ),
    )
    with pytest.raises(RuntimeError):
        fixture.port("metadata-db", 5432)
    fixture.port.cache_clear()
