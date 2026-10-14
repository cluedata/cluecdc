"""Back up, validate, and safely restore ClueCDC PostgreSQL metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    port: int
    username: str
    password: str
    database: str

    @property
    def arguments(self) -> list[str]:
        return [
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--username",
            self.username,
            "--dbname",
            self.database,
        ]


def database_target(value: str) -> DatabaseTarget:
    normalized = value.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise BackupError("Metadata database must be PostgreSQL")
    if not parsed.hostname or not parsed.username or parsed.password is None:
        raise BackupError(
            "Metadata database URL must include host, username, and password"
        )
    database = parsed.path.removeprefix("/")
    if not database or "/" in database:
        raise BackupError("Metadata database URL must identify exactly one database")
    return DatabaseTarget(
        parsed.hostname,
        parsed.port or 5432,
        unquote(parsed.username),
        unquote(parsed.password),
        unquote(database),
    )


def environment_value(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise BackupError(f"Required environment variable {name} is not set")
    return value


def command_prefix(environment_name: str) -> list[str]:
    raw = os.environ.get(environment_name, "[]")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise BackupError(f"{environment_name} must be a JSON string array") from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BackupError(f"{environment_name} must be a JSON string array")
    return value


def safe_error(stderr: bytes, target: DatabaseTarget) -> str:
    message = stderr.decode("utf-8", errors="replace").strip()
    for secret in (target.password,):
        message = message.replace(secret, "[REDACTED]")
    return message[-1000:] or "PostgreSQL command failed without diagnostic output"


def run_tool(
    name: str,
    arguments: list[str],
    target: DatabaseTarget,
    *,
    stdin: bytes | None = None,
    stdout=None,
) -> bytes:
    environment = dict(os.environ)
    environment["PGPASSWORD"] = target.password
    try:
        result = subprocess.run(
            [*command_prefix("CLUECDC_PG_TOOL_PREFIX_JSON"), name, *arguments],
            input=stdin,
            stdout=stdout or subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=environment,
            cwd=ROOT,
        )
    except FileNotFoundError as error:
        raise BackupError(f"Required PostgreSQL tool is unavailable: {name}") from error
    if result.returncode:
        raise BackupError(f"{name} failed: {safe_error(result.stderr, target)}")
    return result.stdout if stdout is None else b""


def query(target: DatabaseTarget, sql: str) -> str:
    output = run_tool(
        "psql",
        [
            *target.arguments,
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--command",
            sql,
        ],
        target,
    )
    return output.decode().strip()


def key_fingerprint(name: str) -> str:
    return hashlib.sha256(environment_value(name).encode()).hexdigest()


def manifest_path(dump: pathlib.Path) -> pathlib.Path:
    return dump.with_name(dump.name + ".manifest.json")


def read_manifest(dump: pathlib.Path) -> dict:
    path = manifest_path(dump)
    if not path.is_file():
        raise BackupError(f"Backup manifest is missing: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackupError("Backup manifest is unreadable or invalid") from error
    if not isinstance(value, dict) or value.get("format") != "cluecdc-metadata-v1":
        raise BackupError("Backup manifest format is unsupported")
    return value


def verify_dump(dump: pathlib.Path, key_env: str) -> dict:
    if not dump.is_file():
        raise BackupError(f"Backup dump is missing: {dump}")
    manifest = read_manifest(dump)
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    if digest != manifest.get("sha256") or dump.stat().st_size != manifest.get(
        "size_bytes"
    ):
        raise BackupError("Backup checksum or size does not match its manifest")
    if key_fingerprint(key_env) != manifest.get("secret_encryption_key_sha256"):
        raise BackupError("Secret encryption key does not match this backup")
    # pg_restore parses the custom archive and detects structural corruption.
    target = DatabaseTarget("localhost", 5432, "validation", "unused", "validation")
    run_tool("pg_restore", ["--list"], target, stdin=dump.read_bytes())
    return manifest


def backup(args: argparse.Namespace) -> None:
    dump = args.output.resolve()
    manifest_file = manifest_path(dump)
    if dump.exists() or manifest_file.exists():
        raise BackupError("Refusing to overwrite an existing backup or manifest")
    dump.parent.mkdir(parents=True, exist_ok=True)
    target = database_target(environment_value(args.database_url_env))
    partial = dump.with_name(dump.name + ".partial")
    try:
        with partial.open("xb") as output:
            run_tool(
                "pg_dump",
                ["--format=custom", "--no-owner", "--no-acl", *target.arguments],
                target,
                stdout=output,
            )
        archive = partial.read_bytes()
        run_tool("pg_restore", ["--list"], target, stdin=archive)
        revision = query(target, "SELECT version_num FROM alembic_version;")
        if not revision:
            raise BackupError("Metadata database has no Alembic revision")
        manifest = {
            "format": "cluecdc-metadata-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(archive).hexdigest(),
            "size_bytes": len(archive),
            "source_database": target.database,
            "alembic_revision": revision,
            "secret_encryption_key_sha256": key_fingerprint(args.secret_key_env),
        }
        partial.replace(dump)
        manifest_file.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        if partial.exists():
            partial.unlink()
    print(f"Metadata backup created and verified: {dump}")


def verify(args: argparse.Namespace) -> None:
    verify_dump(args.dump.resolve(), args.secret_key_env)
    print(f"Metadata backup integrity verified: {args.dump.resolve()}")


def restore(args: argparse.Namespace) -> None:
    dump = args.dump.resolve()
    manifest = verify_dump(dump, args.secret_key_env)
    target_url = environment_value(args.target_database_url_env)
    target = database_target(target_url)
    if args.confirm_target_database != target.database:
        raise BackupError(
            "Target confirmation must exactly match the target database name"
        )
    if (
        manifest.get("source_database") == target.database
        and not args.allow_same_database_name
    ):
        raise BackupError(
            "Target database name matches the source; use an isolated name or explicitly allow it"
        )
    table_count = query(
        target,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog','information_schema');",
    )
    if table_count != "0":
        raise BackupError(
            "Restore target is not empty; refusing to overwrite existing metadata"
        )
    run_tool(
        "pg_restore",
        [
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            *target.arguments,
        ],
        target,
        stdin=dump.read_bytes(),
    )
    migration_environment = dict(os.environ)
    migration_environment["DATABASE_URL"] = target_url
    migration_environment["SECRET_ENCRYPTION_KEY"] = environment_value(
        args.secret_key_env
    )
    application_prefix = command_prefix("CLUECDC_APP_COMMAND_PREFIX_JSON")
    application_cwd = ROOT if application_prefix else ROOT / "apps/api"
    for command in (
        ["-m", "alembic", "upgrade", "head"],
        ["-m", "alembic", "check"],
    ):
        result = subprocess.run(
            [
                *application_prefix,
                "python" if application_prefix else sys.executable,
                *command,
            ],
            cwd=application_cwd,
            env=migration_environment,
            check=False,
            capture_output=True,
        )
        if result.returncode:
            diagnostic = safe_error(result.stderr or result.stdout, target)
            raise BackupError(
                "Restore completed but migration compatibility failed; keep the isolated "
                f"database for diagnosis and do not promote it. {diagnostic}"
            )
    result = subprocess.run(
        [
            *application_prefix,
            "python" if application_prefix else sys.executable,
            "-m",
            "app.verify_restore",
        ],
        cwd=application_cwd,
        env=migration_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        diagnostic = safe_error(
            result.stderr.encode() or result.stdout.encode(), target
        )
        raise BackupError(
            "Restore completed but metadata or encrypted-secret verification failed; "
            f"do not promote the restored database. {diagnostic}"
        )
    print(result.stdout.strip())
    print(f"Metadata restore verified in isolated database: {target.database}")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    backup_command = commands.add_parser("backup")
    backup_command.add_argument("--output", type=pathlib.Path, required=True)
    backup_command.add_argument("--database-url-env", default="DATABASE_URL")
    backup_command.add_argument("--secret-key-env", default="SECRET_ENCRYPTION_KEY")
    backup_command.set_defaults(handler=backup)
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("--dump", type=pathlib.Path, required=True)
    verify_command.add_argument("--secret-key-env", default="SECRET_ENCRYPTION_KEY")
    verify_command.set_defaults(handler=verify)
    restore_command = commands.add_parser("restore")
    restore_command.add_argument("--dump", type=pathlib.Path, required=True)
    restore_command.add_argument("--target-database-url-env", required=True)
    restore_command.add_argument("--secret-key-env", default="SECRET_ENCRYPTION_KEY")
    restore_command.add_argument("--confirm-target-database", required=True)
    restore_command.add_argument("--allow-same-database-name", action="store_true")
    restore_command.set_defaults(handler=restore)
    return value


def main() -> None:
    args = parser().parse_args()
    try:
        args.handler(args)
    except BackupError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
