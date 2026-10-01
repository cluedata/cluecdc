from app.connection_providers.s3 import S3ObjectStorageProvider
from app.core.errors import DomainError
from app.models.entities import Connection

PROVIDERS = {
    "AWS_S3": S3ObjectStorageProvider,
    "MINIO": S3ObjectStorageProvider,
}


def get_connection_provider(connection: Connection, credentials: dict[str, str]):
    implementation = PROVIDERS.get(connection.provider)
    if not implementation:
        raise DomainError("PROVIDER_NOT_IMPLEMENTED", "Connection provider is not implemented", 422)
    return implementation(connection, credentials)


def provider_catalog() -> list[dict]:
    return [
        {
            "provider": "POSTGRESQL",
            "category": "DATABASE",
            "name": "PostgreSQL",
            "description": "PostgreSQL CDC source and JDBC destination",
        },
        {
            "provider": "MYSQL",
            "category": "DATABASE",
            "name": "MySQL",
            "description": "MySQL CDC source and JDBC destination",
        },
        {
            "provider": "SQL_SERVER",
            "category": "DATABASE",
            "name": "SQL Server",
            "description": "SQL Server connection (configuration only)",
        },
        {
            "provider": "ORACLE",
            "category": "DATABASE",
            "name": "Oracle",
            "description": "Oracle connection (configuration only)",
        },
        {
            "provider": "AWS_S3",
            "category": "OBJECT_STORAGE",
            "name": "Amazon S3",
            "description": "AWS-managed S3 object storage",
        },
        {
            "provider": "MINIO",
            "category": "OBJECT_STORAGE",
            "name": "MinIO",
            "description": "Self-hosted S3-compatible object storage",
        },
    ]
