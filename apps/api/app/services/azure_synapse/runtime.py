import asyncio
import hashlib
import json
from contextlib import closing

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.models.entities import (
    Connection,
    KafkaCluster,
    PipelineDestination,
)
from app.services.azure_synapse.connection import _connection_string, _storage_client
from app.services.azure_synapse.planner import classify_error
from app.services.warehouses.format import HISTORY_COLUMNS
from app.services.warehouses.schema import validate_fixed_history_schema

SYNAPSE_HISTORY_TYPES = {
    "_clue_topic": "NVARCHAR",
    "_clue_partition": "INT",
    "_clue_offset": "BIGINT",
    "_clue_timestamp": "BIGINT",
    "_clue_operation": "NVARCHAR",
    "_clue_key": "NVARCHAR",
    "_clue_before": "NVARCHAR",
    "_clue_after": "NVARCHAR",
    "_clue_source": "NVARCHAR",
    "_clue_batch_id": "NVARCHAR",
}


def _quote(value: str) -> str:
    if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise DomainError("INVALID_SQL_IDENTIFIER", "SQL identifier is invalid", 422)
    return "[" + value.replace("]", "]]") + "]"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _names(delivery: PipelineDestination) -> tuple[str, str]:
    suffix = delivery.id.hex[:20]
    return f"_clue_stg_{suffix}", "_cluecdc_batch_markers"


def _table_ddl(schema: str, table: str) -> str:
    columns = (
        "[_clue_topic] nvarchar(249) NOT NULL, [_clue_partition] int NOT NULL, "
        "[_clue_offset] bigint NOT NULL, [_clue_timestamp] bigint NULL, "
        "[_clue_operation] nvarchar(16) NOT NULL, [_clue_key] nvarchar(max) NULL, "
        "[_clue_before] nvarchar(max) NULL, [_clue_after] nvarchar(max) NULL, "
        "[_clue_source] nvarchar(max) NULL, [_clue_batch_id] nvarchar(64) NOT NULL"
    )
    qualified = f"{_quote(schema)}.{_quote(table)}"
    return (
        f"IF OBJECT_ID(N{_literal(qualified)}, N'U') IS NULL BEGIN "
        f"CREATE TABLE {qualified} ({columns}) WITH (DISTRIBUTION = ROUND_ROBIN, HEAP); END"
    )


def _marker_ddl(schema: str, table: str) -> str:
    qualified = f"{_quote(schema)}.{_quote(table)}"
    return (
        f"IF OBJECT_ID(N{_literal(qualified)}, N'U') IS NULL BEGIN CREATE TABLE {qualified} "
        "([delivery_id] nvarchar(36) NOT NULL, [batch_id] nvarchar(64) NOT NULL, "
        "[applied_at] datetime2(6) NOT NULL) WITH (DISTRIBUTION = ROUND_ROBIN, HEAP); END"
    )


def _prepare_sync(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    *,
    auto_create: bool,
) -> None:
    schema = str(connection.config_json["target_schema"])
    staging, marker = _names(delivery)
    database = __import__("mssql_python").connect(
        _connection_string(connection, credentials), timeout=20
    )
    with closing(database):
        cursor = database.cursor()
        try:
            cursor.execute(_marker_ddl(schema, marker))
            cursor.execute(_table_ddl(schema, staging))
            for mapping in delivery.topic_mapping_json:
                if mapping["schema_name"] != schema:
                    raise DomainError(
                        "AZURE_SYNAPSE_SCHEMA_MISMATCH",
                        "Synapse mappings must use the configured target schema",
                        422,
                    )
                qualified = f"{_quote(schema)}.{_quote(mapping['table_name'])}"
                cursor.execute("SELECT OBJECT_ID(?, N'U')", (qualified,))
                present = cursor.fetchone()[0] is not None
                if not present and not auto_create:
                    raise DomainError(
                        "DESTINATION_TABLE_MISSING",
                        "Synapse history table does not exist and auto create is disabled",
                        422,
                        {"table": f"{schema}.{mapping['table_name']}"},
                    )
                if auto_create:
                    cursor.execute(_table_ddl(schema, mapping["table_name"]))
                cursor.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
                    (schema, mapping["table_name"]),
                )
                actual = {str(row[0]): str(row[1]).upper() for row in cursor.fetchall()}
                validate_fixed_history_schema(
                    actual,
                    SYNAPSE_HISTORY_TYPES,
                    provider="AZURE_SYNAPSE",
                    table=f"{schema}.{mapping['table_name']}",
                )
            database.commit()
        finally:
            cursor.close()


async def prepare_destination(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    *,
    auto_create: bool,
) -> None:
    await asyncio.to_thread(
        _prepare_sync,
        connection,
        credentials,
        delivery,
        auto_create=auto_create,
    )


def _stage_sync(
    connection: Connection,
    credentials: dict[str, str],
    staging_key: str,
    body: bytes,
    manifest: dict,
) -> None:
    client = _storage_client(connection, credentials)
    try:
        container = str(connection.config_json["container"])
        client.get_blob_client(container=container, blob=staging_key).upload_blob(
            body, overwrite=True
        )
        client.get_blob_client(
            container=container, blob=staging_key + ".manifest.json"
        ).upload_blob(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
            overwrite=True,
        )
    finally:
        client.close()


def _credential_sql(connection: Connection, credentials: dict[str, str]) -> str:
    method = connection.config_json["storage_authentication"]
    if method == "ACCOUNT_KEY":
        identity, secret = "Storage Account Key", credentials["storage_account_key"]
    elif method == "SAS_TOKEN":
        identity, secret = "Shared Access Signature", credentials["storage_sas_token"].lstrip("?")
    elif method == "SERVICE_PRINCIPAL":
        tenant = str(connection.config_json["tenant_id"])
        client = str(connection.config_json["client_id"])
        identity = f"{client}@https://login.microsoftonline.com/{tenant}/oauth2/token"
        secret = credentials["client_secret"]
    else:
        return "CREDENTIAL = (IDENTITY = 'Managed Identity'), "
    return f"CREDENTIAL = (IDENTITY = {_literal(identity)}, SECRET = {_literal(secret)}), "


def _marker_exists_sync(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    batch_id: str,
) -> bool:
    schema = str(connection.config_json["target_schema"])
    _, marker = _names(delivery)
    database = __import__("mssql_python").connect(
        _connection_string(connection, credentials), timeout=20
    )
    with closing(database):
        cursor = database.cursor()
        try:
            cursor.execute(
                f"SELECT COUNT_BIG(*) FROM {_quote(schema)}.{_quote(marker)} "
                "WHERE [delivery_id] = ? AND [batch_id] = ?",
                (str(delivery.id), batch_id),
            )
            return int(cursor.fetchone()[0]) > 0
        finally:
            cursor.close()


async def marker_exists(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    batch_id: str,
) -> bool:
    return await asyncio.to_thread(_marker_exists_sync, connection, credentials, delivery, batch_id)


def _apply_sync(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    batch_id: str,
    storage_uri: str,
) -> None:
    schema = str(connection.config_json["target_schema"])
    staging, marker = _names(delivery)
    database = __import__("mssql_python").connect(
        _connection_string(connection, credentials), timeout=30
    )
    with closing(database):
        cursor = database.cursor()
        try:
            cursor.execute(f"TRUNCATE TABLE {_quote(schema)}.{_quote(staging)}")
            cursor.execute(
                f"COPY INTO {_quote(schema)}.{_quote(staging)} FROM {_literal(storage_uri)} "
                f"WITH (FILE_TYPE = 'CSV', {_credential_sql(connection, credentials)}"
                "FIELDQUOTE = '\"', ROWTERMINATOR = '0x0a', ENCODING = 'UTF8', MAXERRORS = 0)"
            )
            database.commit()
            cursor.execute(
                f"SELECT COUNT_BIG(*) FROM {_quote(schema)}.{_quote(marker)} "
                "WHERE [delivery_id] = ? AND [batch_id] = ?",
                (str(delivery.id), batch_id),
            )
            if int(cursor.fetchone()[0]) > 0:
                return
            selected = ", ".join(f"s.{_quote(column)}" for column in HISTORY_COLUMNS)
            target_columns = ", ".join(_quote(column) for column in HISTORY_COLUMNS)
            for mapping in delivery.topic_mapping_json:
                target = f"{_quote(schema)}.{_quote(mapping['table_name'])}"
                cursor.execute(
                    f"INSERT INTO {target} ({target_columns}) SELECT {selected} "
                    f"FROM {_quote(schema)}.{_quote(staging)} s WHERE s.[_clue_topic] = ? "
                    f"AND NOT EXISTS (SELECT 1 FROM {target} t WHERE "
                    "t.[_clue_topic] = s.[_clue_topic] AND "
                    "t.[_clue_partition] = s.[_clue_partition] AND "
                    "t.[_clue_offset] = s.[_clue_offset])",
                    (mapping["topic"],),
                )
            cursor.execute(
                f"INSERT INTO {_quote(schema)}.{_quote(marker)} "
                "([delivery_id], [batch_id], [applied_at]) VALUES (?, ?, SYSUTCDATETIME())",
                (str(delivery.id), batch_id),
            )
            database.commit()
        except Exception:
            database.rollback()
            raise
        finally:
            cursor.close()


async def process_once(
    session: AsyncSession,
    delivery: PipelineDestination,
    destination: Connection,
    credentials: dict[str, str],
    kafka: KafkaCluster,
    kafka_credentials: dict[str, str],
) -> dict:
    # Share the existing ledger/checkpoint authority with Redshift while retaining
    # the original Synapse consumer group and provider-specific SQL/storage work.
    from app.services.warehouses.staged import process_staged_once

    async def stage(key: str, body: bytes, manifest: dict) -> dict:
        account = destination.config_json["storage_account"]
        container = destination.config_json["container"]
        manifest = {
            **manifest,
            "sha256": hashlib.sha256(body).hexdigest(),
            "data_uri": f"https://{account}.blob.core.windows.net/{container}/{key}",
        }
        await asyncio.to_thread(_stage_sync, destination, credentials, key, body, manifest)
        return manifest

    async def apply(batch_id: str, uri: str) -> None:
        await asyncio.to_thread(_apply_sync, destination, credentials, delivery, batch_id, uri)

    async def marker(batch_id: str) -> bool:
        return await marker_exists(destination, credentials, delivery, batch_id)

    return await process_staged_once(
        session,
        delivery,
        kafka,
        kafka_credentials,
        provider="AZURE_SYNAPSE",
        staging_prefix=str(destination.config_json["staging_prefix"]),
        stage=stage,
        apply=apply,
        marker_exists=marker,
        classify=classify_error,
        consumer_group_prefix="cluecdc-synapse",
    )
