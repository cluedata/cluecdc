from typing import Protocol

from app.models.entities import Connection


class ConnectionProvider(Protocol):
    async def test_connection(self) -> dict: ...


def secret_reference(connection: Connection, key: str) -> str:
    if not connection.secret_ref:
        return ""
    return f"${{cluecdc:{connection.secret_ref}:{key}}}"
