from app.core.errors import DomainError
from app.providers.base import DatabaseProvider
from app.providers.mysql import MySQLProvider
from app.providers.postgresql import PostgreSQLProvider

_PROVIDERS: dict[str, DatabaseProvider] = {
    "postgresql": PostgreSQLProvider(),
    "mysql": MySQLProvider(),
}


def get_provider(database_type: str):
    provider = _PROVIDERS.get(database_type.lower())
    if not provider:
        raise DomainError(
            "DATABASE_PROVIDER_UNSUPPORTED", "Database provider is not supported", 422
        )
    return provider


def provider_metadata() -> list[dict]:
    return [provider.metadata.as_dict() for provider in _PROVIDERS.values()]


def provider_metadata_for(database_type: str) -> dict:
    return get_provider(database_type).metadata.as_dict()
