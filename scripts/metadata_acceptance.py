"""OPS-01/OPS-02: repeat populated backup/restore and v0.2.0 upgrade in isolated databases."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from cdc_acceptance_support import ROOT, AcceptanceError, Compose
from deployment_runtime import docker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--env-file", action="append", type=Path, required=True)
    args = parser.parse_args()
    compose = Compose(args.compose_project, args.env_file)
    database_id = compose.run("ps", "-q", "metadata-db")
    api_id = compose.run("ps", "-q", "cluecdc-api")
    if not database_id or not api_id:
        raise AcceptanceError(
            "Metadata acceptance requires the isolated integration stack"
        )
    info = json.loads(docker("inspect", api_id))[0]
    values = dict(
        value.split("=", 1) for value in info["Config"]["Env"] if "=" in value
    )
    environment = {
        **os.environ,
        **{
            name: values[name]
            for name in (
                "DATABASE_URL",
                "SECRET_ENCRYPTION_KEY",
                "CONNECT_SECRET_TOKEN",
                "SESSION_SECRET",
                "AUTH_MODE",
                "ENVIRONMENT",
            )
        },
    }
    environment["CLUECDC_PG_TOOL_PREFIX_JSON"] = json.dumps(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "PGPASSWORD",
            database_id,
        ]
    )
    environment["CLUECDC_APP_COMMAND_PREFIX_JSON"] = json.dumps(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "DATABASE_URL",
            "-e",
            "SECRET_ENCRYPTION_KEY",
            api_id,
        ]
    )
    run = uuid.uuid4().hex[:12]
    upgrade_name = "cluecdc_upgrade_" + run
    restore_name = "cluecdc_restore_" + run
    # Explicit run-scoped names; nothing in this script drops or downgrades the source database.
    owned: list[str] = []
    artifacts = ROOT / "artifacts" / ("metadata-" + run)
    artifacts.mkdir(parents=True)

    def command(script: str, *arguments: str, expect_success: bool = True) -> str:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if (result.returncode == 0) != expect_success:
            diagnostic = (result.stderr or result.stdout)[-2000:]
            for key in (
                "SECRET_ENCRYPTION_KEY",
                "CONNECT_SECRET_TOKEN",
                "SESSION_SECRET",
                "DATABASE_URL",
            ):
                if secret := environment.get(key):
                    diagnostic = diagnostic.replace(secret, "[REDACTED]")
            raise AcceptanceError(
                f"Metadata {script} {arguments[0]} unexpected result: {diagnostic}"
            )
        return result.stdout

    def sql(value: str) -> str:
        return compose.exec(
            "metadata-db",
            ["psql", "-U", "cluecdc", "-d", "cluecdc", "-qAt", "-v", "ON_ERROR_STOP=1"],
            value,
        )

    def app(*arguments: str) -> str:
        prefix = json.loads(environment["CLUECDC_APP_COMMAND_PREFIX_JSON"])
        result = subprocess.run(
            [*prefix, "python", *arguments],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if result.returncode:
            raise AcceptanceError(
                "Isolated metadata application verification failed; output withheld"
            )
        return result.stdout

    original_url = values["DATABASE_URL"]
    source_dump = artifacts / "populated-head.dump"
    upgrade_dump = artifacts / "v020.dump"
    try:
        command("metadata_backup.py", "backup", "--output", str(source_dump))
        for name in (upgrade_name, restore_name):
            sql(f'CREATE DATABASE "{name}";')
            owned.append(name)
        environment["TARGET_DATABASE_URL"] = (
            original_url.rsplit("/", 1)[0] + "/" + upgrade_name
        )
        command(
            "metadata_backup.py",
            "restore",
            "--dump",
            str(source_dump),
            "--target-database-url-env",
            "TARGET_DATABASE_URL",
            "--confirm-target-database",
            upgrade_name,
        )
        environment["DATABASE_URL"] = environment["TARGET_DATABASE_URL"]
        app("-m", "alembic", "downgrade", "0003")
        # Force non-empty auth state in the v0.2.0 fixture; all other entities come from live CDC.
        compose.exec(
            "metadata-db",
            [
                "psql",
                "-U",
                "cluecdc",
                "-d",
                upgrade_name,
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            f"""
            INSERT INTO auth_sessions (id, created_at, updated_at, token_hash, user_id, expires_at)
            SELECT '{uuid.uuid4()}', now(), now(), '{uuid.uuid4().hex}', id, now()+interval '1 day'
            FROM users LIMIT 1;
            INSERT INTO invites (id, created_at, updated_at, email, role, token_hash, expires_at, created_by)
            SELECT '{uuid.uuid4()}', now(), now(), 'upgrade-{run}@example.com', 'VIEWER',
                   '{uuid.uuid4().hex}', now()+interval '1 day', id FROM users LIMIT 1;
        """,
        )
        before = json.loads(app("-m", "app.migration_snapshot").splitlines()[-1])
        if before["revision"] != "0003" or any(
            count < 1 for count in before["counts"].values()
        ):
            raise AcceptanceError(
                "v0.2.0 fixture must contain every preserved metadata category"
            )
        command("metadata_backup.py", "backup", "--output", str(upgrade_dump))
        command("metadata_upgrade.py", "preflight")
        for _ in range(2):
            command(
                "metadata_upgrade.py", "upgrade", "--backup-dump", str(upgrade_dump)
            )
        after = json.loads(app("-m", "app.migration_snapshot").splitlines()[-1])
        if after["revision"] == "0003":
            raise AcceptanceError("Upgrade did not reach the current revision")
        environment["TARGET_DATABASE_URL"] = (
            original_url.rsplit("/", 1)[0] + "/" + restore_name
        )
        command(
            "metadata_backup.py",
            "restore",
            "--dump",
            str(upgrade_dump),
            "--target-database-url-env",
            "TARGET_DATABASE_URL",
            "--confirm-target-database",
            restore_name,
        )
        # Existing target protection is tested against the exact already-restored database.
        command(
            "metadata_backup.py",
            "restore",
            "--dump",
            str(upgrade_dump),
            "--target-database-url-env",
            "TARGET_DATABASE_URL",
            "--confirm-target-database",
            restore_name,
            expect_success=False,
        )
        environment["SECRET_ENCRYPTION_KEY"] = "wrong-key"
        command(
            "metadata_backup.py",
            "verify",
            "--dump",
            str(upgrade_dump),
            expect_success=False,
        )
        (ROOT / "artifacts/metadata-acceptance.json").write_text(
            json.dumps(
                {
                    "requirements": ["OPS-01", "OPS-02"],
                    "status": "PASS",
                    "from_revision": before["revision"],
                    "to_revision": after["revision"],
                    "counts": before["counts"],
                    "idempotent": True,
                    "wrong_key_rejected": True,
                    "nonempty_target_rejected": True,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            "Populated metadata backup/restore, v0.2.0 upgrade and negative guards passed"
        )
    finally:
        for name in reversed(owned):
            if name not in {upgrade_name, restore_name} or not name.endswith(run):
                raise AcceptanceError("Unexpected cleanup target")
            sql(f'DROP DATABASE "{name}" WITH (FORCE);')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
