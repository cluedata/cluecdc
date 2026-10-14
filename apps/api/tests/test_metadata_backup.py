import argparse
import hashlib
import json

import pytest
from scripts import metadata_backup


def test_database_url_password_is_environment_only():
    target = metadata_backup.database_target(
        "postgresql+asyncpg://cluecdc:p%40ssword@metadata.example:5433/control"
    )
    assert target.password == "p@ssword"
    assert target.database == "control" and target.port == 5433
    assert target.password not in target.arguments


def test_verify_rejects_corrupted_archive_before_pg_restore(tmp_path, monkeypatch):
    dump = tmp_path / "metadata.dump"
    dump.write_bytes(b"corrupted")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "correct-key")
    metadata_backup.manifest_path(dump).write_text(
        json.dumps(
            {
                "format": "cluecdc-metadata-v1",
                "sha256": hashlib.sha256(b"original").hexdigest(),
                "size_bytes": len(b"original"),
                "secret_encryption_key_sha256": hashlib.sha256(b"correct-key").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        metadata_backup,
        "run_tool",
        lambda *args, **kwargs: pytest.fail("pg_restore must not run after checksum failure"),
    )
    with pytest.raises(metadata_backup.BackupError, match="checksum"):
        metadata_backup.verify_dump(dump, "SECRET_ENCRYPTION_KEY")


def test_backup_refuses_to_overwrite_existing_artifact(tmp_path):
    dump = tmp_path / "metadata.dump"
    dump.write_bytes(b"existing")
    args = argparse.Namespace(
        output=dump,
        database_url_env="DATABASE_URL",
        secret_key_env="SECRET_ENCRYPTION_KEY",
    )
    with pytest.raises(metadata_backup.BackupError, match="overwrite"):
        metadata_backup.backup(args)


def test_restore_requires_exact_target_confirmation(tmp_path, monkeypatch):
    dump = tmp_path / "metadata.dump"
    monkeypatch.setattr(
        metadata_backup,
        "verify_dump",
        lambda *_: {"source_database": "cluecdc"},
    )
    monkeypatch.setenv(
        "RESTORE_DATABASE_URL",
        "postgresql+asyncpg://operator:password@db/cluecdc_restore",
    )
    args = argparse.Namespace(
        dump=dump,
        secret_key_env="SECRET_ENCRYPTION_KEY",
        target_database_url_env="RESTORE_DATABASE_URL",
        confirm_target_database="wrong-target",
        allow_same_database_name=False,
    )
    with pytest.raises(metadata_backup.BackupError, match="confirmation"):
        metadata_backup.restore(args)
