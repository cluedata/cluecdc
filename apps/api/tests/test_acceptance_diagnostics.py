import io
import json
import urllib.error

import pytest
from scripts.cdc_acceptance_support import AcceptanceError, ApiClient, fixture_table_mapping


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


@pytest.mark.parametrize("reverse_tables", [False, True])
@pytest.mark.parametrize("reverse_mappings", [False, True])
def test_failure_fixture_uses_identity_when_api_collections_are_reordered(
    reverse_tables, reverse_mappings
):
    """TEST-01/REL-01: random resource IDs must not direct account mutations at ledger tables."""
    tables = [
        {"table_name": name, "topic_name": "fixture.public." + name}
        for name in ("accounts", "ledger")
    ]
    mappings = [
        {"topic": "fixture.public." + name, "table_name": "target_" + name}
        for name in ("accounts", "ledger")
    ]
    if reverse_tables:
        tables.reverse()
    if reverse_mappings:
        mappings.reverse()
    source, mapping = fixture_table_mapping(
        {"tables": tables}, {"topic_mapping_json": mappings}, "accounts"
    )
    assert source["table_name"] == "accounts"
    assert mapping["table_name"] == "target_accounts"
