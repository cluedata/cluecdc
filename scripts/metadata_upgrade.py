"""Preflight and apply supported ClueCDC metadata upgrades safely."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

if __package__:
    from .metadata_backup import (
        BackupError,
        command_prefix,
        database_target,
        environment_value,
        query,
        verify_dump,
    )
else:
    from metadata_backup import (
        BackupError,
        command_prefix,
        database_target,
        environment_value,
        query,
        verify_dump,
    )

ROOT = pathlib.Path(__file__).resolve().parents[1]
SUPPORTED_REVISIONS = {"5e2d8a9f1c30", "0001", "0002", "0003", "0004", "0005", "0006"}


class UpgradeError(RuntimeError):
    pass


def application(command: list[str], environment: dict[str, str]) -> str:
    prefix = command_prefix("CLUECDC_APP_COMMAND_PREFIX_JSON")
    executable = "python" if prefix else sys.executable
    result = subprocess.run(
        [*prefix, executable, *command],
        cwd=ROOT if prefix else ROOT / "apps/api",
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise UpgradeError(
            "Upgrade command failed; do not retry blindly. Restore the verified backup "
            "into an isolated database and inspect migration logs."
        )
    return result.stdout.strip()


def environment(args: argparse.Namespace) -> tuple[dict[str, str], str]:
    database_url = environment_value(args.database_url_env)
    values = dict(os.environ)
    values["DATABASE_URL"] = database_url
    values["SECRET_ENCRYPTION_KEY"] = environment_value(args.secret_key_env)
    return values, database_url


def sanitized_snapshot(values: dict[str, str]) -> dict:
    output = application(["-m", "app.migration_snapshot"], values)
    try:
        return json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise UpgradeError(
            "Migration preflight returned an invalid snapshot"
        ) from error


def preflight(args: argparse.Namespace) -> dict:
    values, database_url = environment(args)
    target = database_target(database_url)
    revision = query(target, "SELECT version_num FROM alembic_version;")
    if revision not in SUPPORTED_REVISIONS:
        raise UpgradeError(
            f"Unsupported metadata revision {revision!r}; use documented export/rebuild recovery"
        )
    snapshot = sanitized_snapshot(values)
    if snapshot.get("revision") != revision:
        raise UpgradeError("Alembic revision changed during preflight")
    if args.snapshot_output:
        output = args.snapshot_output.resolve()
        if output.exists():
            raise UpgradeError("Refusing to overwrite an existing preflight snapshot")
        output.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"Metadata upgrade preflight passed at revision {revision}")
    return snapshot


def upgrade(args: argparse.Namespace) -> None:
    if args.backup_dump is None:
        raise UpgradeError("Upgrade requires --backup-dump from a completed backup")
    verify_dump(args.backup_dump.resolve(), args.secret_key_env)
    before = preflight(args)
    values, _ = environment(args)
    application(["-m", "alembic", "upgrade", "head"], values)
    application(["-m", "alembic", "check"], values)
    after = sanitized_snapshot(values)
    for field in ("counts", "identity_sha256", "secret_ciphertext_sha256"):
        if before.get(field) != after.get(field):
            raise UpgradeError(
                f"Post-upgrade {field} changed unexpectedly; do not promote this database"
            )
    print(f"Metadata upgrade verified: {before['revision']} -> {after['revision']}")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    for name, handler in (("preflight", preflight), ("upgrade", upgrade)):
        command = commands.add_parser(name)
        command.add_argument("--database-url-env", default="DATABASE_URL")
        command.add_argument("--secret-key-env", default="SECRET_ENCRYPTION_KEY")
        command.add_argument("--snapshot-output", type=pathlib.Path)
        if name == "upgrade":
            command.add_argument("--backup-dump", type=pathlib.Path)
        command.set_defaults(handler=handler)
    return value


def main() -> None:
    args = parser().parse_args()
    try:
        args.handler(args)
    except (BackupError, UpgradeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
