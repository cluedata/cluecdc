from app.core.errors import DomainError
from app.providers.base import DatabaseProvider
from app.providers.extended import MongoDBProvider, OracleProvider, SQLServerProvider
from app.providers.mariadb import MariaDBProvider
from app.providers.mysql import MySQLProvider
from app.providers.postgresql import PostgreSQLProvider

_PROVIDERS: dict[str, DatabaseProvider] = {
    "postgresql": PostgreSQLProvider(),
    "mysql": MySQLProvider(),
    "mariadb": MariaDBProvider(),
    "sqlserver": SQLServerProvider(),
    "mongodb": MongoDBProvider(),
    "oracle": OracleProvider(),
}


def get_provider(database_type: str):
    provider = _PROVIDERS.get(database_type.lower())
    if not provider:
        raise DomainError(
            "DATABASE_PROVIDER_UNSUPPORTED", "Database provider is not supported", 422
        )
    return provider


def provider_metadata() -> list[dict]:
    return [provider_metadata_for(name) for name in _PROVIDERS]


def provider_metadata_for(database_type: str) -> dict:
    from app.providers.source_capabilities import source_capabilities

    return {
        **get_provider(database_type).metadata.as_dict(),
        "source_capabilities": source_capabilities(database_type.lower()),
    }
