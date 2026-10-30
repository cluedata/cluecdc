import argparse

import pytest
from scripts import metadata_upgrade


def arguments(**overrides):
    values = {
        "database_url_env": "DATABASE_URL",
        "secret_key_env": "SECRET_ENCRYPTION_KEY",
        "snapshot_output": None,
        "backup_dump": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_preflight_rejects_unknown_revision_before_snapshot(monkeypatch):
    monkeypatch.setattr(
        metadata_upgrade,
        "environment",
        lambda args: ({}, "postgresql://operator:password@db/metadata"),
    )
    monkeypatch.setattr(metadata_upgrade, "query", lambda target, sql: "future-revision")
    monkeypatch.setattr(
        metadata_upgrade,
        "sanitized_snapshot",
        lambda values: pytest.fail("snapshot must not run for an unknown revision"),
    )
    with pytest.raises(metadata_upgrade.UpgradeError, match="Unsupported metadata revision"):
        metadata_upgrade.preflight(arguments())


def test_current_revision_is_supported_for_idempotent_upgrade(monkeypatch):
    snapshot = {"revision": "0013"}
    monkeypatch.setattr(
        metadata_upgrade,
        "environment",
        lambda args: ({}, "postgresql://operator:password@db/metadata"),
    )
    monkeypatch.setattr(metadata_upgrade, "query", lambda target, sql: "0013")
    monkeypatch.setattr(metadata_upgrade, "sanitized_snapshot", lambda values: snapshot)
    assert metadata_upgrade.preflight(arguments()) == snapshot


def test_upgrade_requires_verified_backup():
    with pytest.raises(metadata_upgrade.UpgradeError, match="requires --backup-dump"):
        metadata_upgrade.upgrade(arguments())


def test_upgrade_detects_identity_drift(monkeypatch, tmp_path):
    backup = tmp_path / "metadata.dump"
    before = {
        "revision": "0003",
        "counts": {"users": 1},
        "identity_sha256": {"users": "before"},
        "secret_ciphertext_sha256": "ciphertext",
    }
    after = {
        **before,
        "revision": "0006",
        "identity_sha256": {"users": "after"},
    }
    monkeypatch.setattr(metadata_upgrade, "verify_dump", lambda *args: {})
    monkeypatch.setattr(metadata_upgrade, "preflight", lambda args: before)
    monkeypatch.setattr(metadata_upgrade, "environment", lambda args: ({}, "url"))
    monkeypatch.setattr(metadata_upgrade, "application", lambda *args: "")
    monkeypatch.setattr(metadata_upgrade, "sanitized_snapshot", lambda values: after)
    with pytest.raises(metadata_upgrade.UpgradeError, match="identity_sha256"):
        metadata_upgrade.upgrade(arguments(backup_dump=backup))
