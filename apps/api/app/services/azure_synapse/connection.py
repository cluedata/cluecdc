import asyncio
import uuid
from contextlib import closing
from typing import Any

from app.core.errors import DomainError
from app.models.entities import Connection


def _odbc(value: str) -> str:
    """Quote an ODBC value so credentials cannot add connection properties."""
    return "{" + value.replace("}", "}}") + "}"


def _connection_string(connection: Connection, credentials: dict[str, str]) -> str:
    config = connection.config_json
    parts = [
        f"Server=tcp:{config['sql_endpoint']},{config.get('port', 1433)}",
        f"Database={_odbc(str(config['database_name']))}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
    ]
    authentication = config["authentication"]
    if authentication == "SQL_PASSWORD":
        parts.extend(
            [
                "Authentication=SqlPassword",
                f"UID={_odbc(str(config['username']))}",
                f"PWD={_odbc(credentials['password'])}",
            ]
        )
    elif authentication == "SERVICE_PRINCIPAL":
        parts.extend(
            [
                "Authentication=ActiveDirectoryServicePrincipal",
                f"UID={_odbc(str(config['client_id']))}",
                f"PWD={_odbc(credentials['client_secret'])}",
            ]
        )
    else:
        parts.append("Authentication=ActiveDirectoryMSI")
        if config.get("managed_identity_client_id"):
            parts.append(f"UID={_odbc(str(config['managed_identity_client_id']))}")
    return ";".join(parts) + ";"


def _storage_client(connection: Connection, credentials: dict[str, str]):
    from azure.identity import ClientSecretCredential, ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient

    config = connection.config_json
    account = config["storage_account"]
    endpoint = f"https://{account}.blob.core.windows.net"
    method = config["storage_authentication"]
    if method == "ACCOUNT_KEY":
        credential: Any = credentials["storage_account_key"]
    elif method == "SAS_TOKEN":
        credential = credentials["storage_sas_token"].lstrip("?")
    elif method == "SERVICE_PRINCIPAL":
        credential = ClientSecretCredential(
            tenant_id=config["tenant_id"],
            client_id=config["client_id"],
            client_secret=credentials["client_secret"],
        )
    else:
        credential = ManagedIdentityCredential(
            client_id=config.get("managed_identity_client_id") or None
        )
    return BlobServiceClient(
        account_url=endpoint,
        credential=credential,
        connection_timeout=10,
        read_timeout=10,
    )


def _test_sql(connection: Connection, credentials: dict[str, str]) -> list[dict]:
    try:
        import mssql_python

        database = mssql_python.connect(_connection_string(connection, credentials), timeout=15)
        with closing(database):
            cursor = database.cursor()
            try:
                cursor.execute(
                    "SELECT CAST(SERVERPROPERTY('EngineEdition') AS int), DB_NAME(), "
                    "CAST(DATABASEPROPERTYEX(DB_NAME(), 'Status') AS nvarchar(128)), "
                    "SCHEMA_ID(?), "
                    "HAS_PERMS_BY_NAME(?, 'SCHEMA', 'SELECT'), "
                    "HAS_PERMS_BY_NAME(?, 'SCHEMA', 'INSERT'), "
                    "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE')",
                    (
                        connection.config_json["target_schema"],
                        connection.config_json["target_schema"],
                        connection.config_json["target_schema"],
                    ),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError(
            "AZURE_SYNAPSE_CONFIG_INVALID", "Azure Synapse SQL configuration is invalid", 422
        ) from exc
    except Exception as exc:
        message = str(exc).lower()
        if "42108" in message or "paused" in message:
            raise DomainError(
                "AZURE_SYNAPSE_POOL_PAUSED",
                "The Dedicated SQL Pool is paused; ClueCDC will not resume paid compute",
                503,
            ) from None
        if "18456" in message or "login failed" in message or "token" in message:
            raise DomainError(
                "AZURE_SYNAPSE_AUTH_FAILED", "Azure Synapse rejected the supplied identity", 422
            ) from None
        raise DomainError(
            "AZURE_SYNAPSE_SQL_UNAVAILABLE",
            "Azure Synapse SQL endpoint connectivity failed",
            503,
        ) from None
    if row is None:
        raise DomainError(
            "AZURE_SYNAPSE_SQL_UNAVAILABLE", "Azure Synapse returned no validation result", 503
        )
    engine, database_name, state, schema_id, can_select, can_insert, can_create = row
    if int(engine) != 6:
        raise DomainError(
            "AZURE_SYNAPSE_DEDICATED_REQUIRED",
            "The destination must be an Azure Synapse Dedicated SQL Pool",
            422,
        )
    if str(database_name) != str(connection.config_json["database_name"]):
        raise DomainError(
            "AZURE_SYNAPSE_DATABASE_MISMATCH", "Connected to an unexpected SQL database", 422
        )
    if str(state).upper() != "ONLINE":
        raise DomainError(
            "AZURE_SYNAPSE_POOL_UNAVAILABLE",
            "The Dedicated SQL Pool is not online",
            503,
            {"state": str(state)},
        )
    if schema_id is None:
        raise DomainError(
            "AZURE_SYNAPSE_SCHEMA_MISSING", "The configured target schema does not exist", 422
        )
    if not all((can_select, can_insert, can_create)):
        raise DomainError(
            "AZURE_SYNAPSE_PERMISSION_DENIED",
            "The destination identity lacks required schema or table-creation permissions",
            422,
        )
    return [
        {"name": "sql_connectivity", "status": "success"},
        {"name": "authentication", "status": "success"},
        {"name": "dedicated_sql_pool", "status": "success"},
        {"name": "database_schema", "status": "success"},
        {"name": "destination_permissions", "status": "success"},
    ]


def _test_storage(connection: Connection, credentials: dict[str, str]) -> list[dict]:
    client = None
    blob = None
    uploaded = False
    try:
        client = _storage_client(connection, credentials)
        config = connection.config_json
        key = f"{config['staging_prefix']}/.cluecdc/connection-tests/{uuid.uuid4().hex}.probe"
        blob = client.get_blob_client(container=config["container"], blob=key)
        blob.upload_blob(b"cluecdc storage test\n", overwrite=False)
        uploaded = True
        blob.get_blob_properties()
        blob.delete_blob()
        uploaded = False
        return [
            {"name": "storage_access", "status": "success"},
            {"name": "staging_permissions", "status": "success"},
            {"name": "staging_cleanup", "status": "success"},
        ]
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403} or "authentication" in type(exc).__name__.lower():
            raise DomainError(
                "AZURE_STORAGE_PERMISSION_DENIED",
                "Azure Storage authentication or staging-prefix permission failed",
                422,
            ) from None
        raise DomainError(
            "AZURE_STORAGE_UNAVAILABLE", "Azure Storage staging connectivity failed", 503
        ) from None
    finally:
        if uploaded and blob is not None:
            try:
                blob.delete_blob()
            except Exception:
                raise DomainError(
                    "AZURE_STORAGE_TEST_CLEANUP_FAILED",
                    "The temporary staging probe could not be removed",
                    503,
                ) from None
        if client is not None:
            client.close()


async def test_connection(connection: Connection, credentials: dict[str, str]) -> dict:
    """Validate SQL and storage independently without touching production tables."""
    sql_checks = await asyncio.to_thread(_test_sql, connection, credentials)
    storage_checks = await asyncio.to_thread(_test_storage, connection, credentials)
    return {
        "success": True,
        "status": "HEALTHY",
        "checks": sql_checks
        + storage_checks
        + [{"name": "loading_mode", "status": "success", "message": "staged COPY"}],
    }
