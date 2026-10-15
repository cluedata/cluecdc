import shutil

import pytest
from scripts import deployment_validation


def test_production_kustomize_satisfies_hardening_contract():
    """OPS-03: rendered production resources must satisfy the deployment contract."""
    if shutil.which("kubectl") is None:
        pytest.skip("kubectl unavailable; mandatory CI deployment gate reports infrastructure")
    assert deployment_validation.validate(deployment_validation.render()) == []


def test_deployment_validation_rejects_bundled_metadata():
    """OPS-03: production must not silently fall back to a bundled database."""
    errors = deployment_validation.validate(
        [{"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": "db"}}]
    )
    assert "production base must use external metadata PostgreSQL" in errors
