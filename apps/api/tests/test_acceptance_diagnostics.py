import io
import json
import urllib.error

import pytest
from scripts.cdc_acceptance_support import AcceptanceError, ApiClient


def test_acceptance_http_diagnostics_preserve_error_fields_without_exposing_secrets(monkeypatch):
    """TEST-01/SEC-04: useful integration failures must redact Unicode credentials too."""
    secret = "mật-khẩu-private"
    payload = {
        "error": {
            "code": "CONNECTOR_CONFIG_INVALID",
            "message": "Invalid config",
            "details": {
                "errors": [{"name": "producer.override.ssl.truststore.location", "message": secret}]
            },
        }
    }

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://fixture/pipelines", 422, "invalid", {}, io.BytesIO(json.dumps(payload).encode())
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    client = ApiClient("http://fixture", [secret])
    with pytest.raises(AcceptanceError) as captured:
        client.create("/pipelines", {})
    diagnostic = str(captured.value)
    assert "CONNECTOR_CONFIG_INVALID" in diagnostic
    assert "producer.override.ssl.truststore.location" in diagnostic
    assert "[REDACTED]" in diagnostic
    assert secret not in diagnostic and json.dumps(secret)[1:-1] not in diagnostic
