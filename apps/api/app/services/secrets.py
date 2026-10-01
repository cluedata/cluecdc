import json
from typing import Any, Protocol
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.entities import SecretReference
from app.repositories.metadata import get


class SecretProvider(Protocol):
    async def put_secret(self, value: dict[str, Any]) -> UUID: ...
    async def get_secret(self, ref: UUID) -> dict[str, Any]: ...


class EncryptedDatabaseSecretProvider:
    def __init__(self, session: AsyncSession):
        self.session = session
        key = get_settings().secret_encryption_key.get_secret_value()
        self.fernet = Fernet(key.encode())

    async def put_secret(self, value: dict) -> UUID:
        secret = SecretReference(
            ciphertext=self.fernet.encrypt(json.dumps(value).encode()).decode()
        )
        self.session.add(secret)
        await self.session.flush()
        return secret.id

    async def get_secret(self, ref: UUID) -> dict:
        secret = await get(self.session, SecretReference, ref)
        return json.loads(self.fernet.decrypt(secret.ciphertext.encode()))


def secret_provider(session: AsyncSession) -> SecretProvider:
    """Community provider seam; extensions can replace this factory at composition time."""
    return EncryptedDatabaseSecretProvider(session)
