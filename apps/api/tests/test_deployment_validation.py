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


def test_kustomize_does_not_pollute_external_network_policy_selectors():
    """OPS-03: ingress controllers and external Connect pods do not carry application labels."""
    if shutil.which("kubectl") is None:
        pytest.skip("kubectl unavailable")
    for document in deployment_validation.render():
        if document["kind"] != "NetworkPolicy":
            continue
        for rule in document["spec"].get("ingress", []):
            for peer in rule.get("from", []):
                if "namespaceSelector" in peer:
                    assert "app.kubernetes.io/part-of" not in peer.get("podSelector", {}).get(
                        "matchLabels", {}
                    )


def test_validator_rejects_exposing_private_api_and_open_ingress():
    """OPS-03: regressions cannot publish API or replace default deny with allow-all."""
    documents = [
        {
            "kind": "Ingress",
            "spec": {
                "tls": [{}],
                "rules": [{"http": {"paths": [{"backend": {"service": {"name": "cluecdc-api"}}}]}}],
            },
        },
        {
            "kind": "NetworkPolicy",
            "metadata": {"name": "default-deny-ingress"},
            "spec": {"podSelector": {}, "ingress": [{}]},
        },
    ]
    errors = deployment_validation.validate(documents)
    assert "public Ingress may route only to the web service" in errors
    assert "default-deny-ingress must deny all namespace pod ingress" in errors
