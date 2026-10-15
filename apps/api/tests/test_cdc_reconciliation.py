import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from cdc_acceptance_support import AcceptanceError, reconcile_records  # noqa: E402


def test_reconciliation_normalizes_decimal_and_timezone() -> None:
    reconcile_records(
        [
            {
                "id": 1,
                "name": "Xin chào 👋",
                "amount": "123.4500",
                "occurred_at": "2026-10-14T12:30:00+07:00",
            }
        ],
        [
            {
                "id": 1,
                "name": "Xin chào 👋",
                "amount": "123.45",
                "occurred_at": "2026-10-14T05:30:00Z",
            }
        ],
        key_fields=["id"],
        decimal_fields=["amount"],
        timestamp_fields=["occurred_at"],
    )


def test_reconciliation_reports_keys_and_fields_without_row_values() -> None:
    with pytest.raises(AcceptanceError) as failure:
        reconcile_records(
            [{"id": 1, "secret_note": "source"}, {"id": 2, "secret_note": None}],
            [{"id": 1, "secret_note": "destination"}, {"id": 3, "secret_note": None}],
            key_fields=["id"],
        )

    message = str(failure.value)
    assert "missing=[(2,)]" in message
    assert "unexpected=[(3,)]" in message
    assert "secret_note" in message
    assert "source" not in message
    assert "destination" not in message


def test_reconciliation_rejects_duplicate_keys() -> None:
    with pytest.raises(AcceptanceError, match="Duplicate reconciliation key"):
        reconcile_records(
            [{"id": 1}, {"id": 1}],
            [{"id": 1}],
            key_fields=["id"],
        )
