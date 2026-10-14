"""Read-only validation for an isolated restored metadata database."""

import asyncio

from sqlalchemy import func, select

from app.core.database import Session
from app.models.entities import (
    AuditLog,
    Connection,
    Pipeline,
    PipelineDestination,
    SecretReference,
    User,
)
from app.services.secrets import EncryptedDatabaseSecretProvider


async def verify() -> None:
    async with Session() as session:
        references = (await session.scalars(select(SecretReference.id))).all()
        provider = EncryptedDatabaseSecretProvider(session)
        for identifier in references:
            value = await provider.get_secret(identifier)
            if not isinstance(value, dict) or not value:
                raise RuntimeError("An encrypted secret did not decode to a non-empty mapping")
        models = (User, Connection, Pipeline, PipelineDestination, AuditLog)
        counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in models
        }
    print(
        "Restored metadata and encrypted secrets verified "
        f"({len(references)} secret references; table counts: {counts})"
    )


def main() -> None:
    asyncio.run(verify())


if __name__ == "__main__":
    main()
