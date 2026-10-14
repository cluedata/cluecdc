"""Emit a sanitized identity snapshot for metadata upgrade verification."""

import asyncio
import hashlib
import json

from sqlalchemy import text

from app.core.database import Session
from app.services.secrets import EncryptedDatabaseSecretProvider

TABLES = (
    "users",
    "auth_sessions",
    "invites",
    "connections",
    "pipelines",
    "pipeline_destinations",
    "secret_references",
    "alerts",
    "audit_logs",
)


def digest(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


async def snapshot() -> dict:
    async with Session() as session:
        revision = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        identities = {}
        counts = {}
        for table in TABLES:
            identifiers = list(
                (
                    await session.execute(text(f'SELECT id::text FROM "{table}" ORDER BY id'))
                ).scalars()
            )
            counts[table] = len(identifiers)
            identities[table] = digest(identifiers)
        ciphertext_rows = (
            await session.execute(
                text("SELECT id::text, ciphertext FROM secret_references ORDER BY id")
            )
        ).all()
        ciphertext_digest = digest([f"{row[0]}:{row[1]}" for row in ciphertext_rows])
        provider = EncryptedDatabaseSecretProvider(session)
        for identifier in (await session.scalars(text("SELECT id FROM secret_references"))).all():
            value = await provider.get_secret(identifier)
            if not isinstance(value, dict) or not value:
                raise RuntimeError("Encrypted secret verification failed")
    return {
        "revision": revision,
        "counts": counts,
        "identity_sha256": identities,
        "secret_ciphertext_sha256": ciphertext_digest,
    }


def main() -> None:
    print(json.dumps(asyncio.run(snapshot()), sort_keys=True))


if __name__ == "__main__":
    main()
