import asyncio
import hashlib
import json
from contextlib import closing

from app.core.errors import DomainError
from app.models.entities import Connection, KafkaCluster, PipelineDestination
from app.services.redshift import _aws_credentials, connection_options
from app.services.warehouses.format import HISTORY_COLUMNS
from app.services.warehouses.schema import validate_fixed_history_schema
from app.services.warehouses.staged import process_staged_once

REDSHIFT_HISTORY_TYPES = {
    "_clue_topic": "CHARACTER VARYING",
    "_clue_partition": "INTEGER",
    "_clue_offset": "BIGINT",
    "_clue_timestamp": "BIGINT",
    "_clue_operation": "CHARACTER VARYING",
    "_clue_key": "CHARACTER VARYING",
    "_clue_before": "CHARACTER VARYING",
    "_clue_after": "CHARACTER VARYING",
    "_clue_source": "CHARACTER VARYING",
    "_clue_batch_id": "CHARACTER VARYING",
}


def _quote(value: str) -> str:
    if not value or len(value) > 127 or any(ord(char) < 32 for char in value):
        raise DomainError("INVALID_SQL_IDENTIFIER", "SQL identifier is invalid", 422)
    return '"' + value.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _names(delivery: PipelineDestination) -> tuple[str, str]:
    return f"_clue_stg_{delivery.id.hex[:20]}", "_cluecdc_batch_markers"


def _columns() -> str:
    return (
        '"_clue_topic" VARCHAR(249) NOT NULL, "_clue_partition" INTEGER NOT NULL, '
        '"_clue_offset" BIGINT NOT NULL, "_clue_timestamp" BIGINT, '
        '"_clue_operation" VARCHAR(16) NOT NULL, "_clue_key" VARCHAR(MAX), '
        '"_clue_before" VARCHAR(MAX), "_clue_after" VARCHAR(MAX), '
        '"_clue_source" VARCHAR(MAX), "_clue_batch_id" VARCHAR(64) NOT NULL'
    )


def _prepare_sync(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    auto_create: bool,
) -> None:
    import redshift_connector

    schema = str(connection.config_json["schema_name"])
    staging, marker = _names(delivery)
    with closing(redshift_connector.connect(**connection_options(connection, credentials))) as db:
        cursor = db.cursor()
        try:
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {_quote(schema)}.{_quote(marker)} "
                "(delivery_id VARCHAR(36) NOT NULL, batch_id VARCHAR(64) NOT NULL, "
                "applied_at TIMESTAMP NOT NULL DEFAULT GETDATE()) DISTSTYLE AUTO"
            )
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {_quote(schema)}.{_quote(staging)} "
                f"({_columns()}) DISTSTYLE AUTO"
            )
            for mapping in delivery.topic_mapping_json:
                if mapping["schema_name"] != schema:
                    raise DomainError(
                        "REDSHIFT_SCHEMA_MISMATCH",
                        "Redshift mappings must use the configured target schema",
                        422,
                    )
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=%s AND table_name=%s",
                    (schema, mapping["table_name"]),
                )
                present = int(cursor.fetchone()[0]) > 0
                if not present and not auto_create:
                    raise DomainError(
                        "DESTINATION_TABLE_MISSING",
                        "Redshift history table does not exist and auto create is disabled",
                        422,
                    )
                if auto_create:
                    cursor.execute(
                        f"CREATE TABLE IF NOT EXISTS {_quote(schema)}."
                        f"{_quote(mapping['table_name'])} ({_columns()}) DISTSTYLE AUTO"
                    )
                cursor.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s",
                    (schema, mapping["table_name"]),
                )
                actual = {str(row[0]): str(row[1]).upper() for row in cursor.fetchall()}
                validate_fixed_history_schema(
                    actual,
                    REDSHIFT_HISTORY_TYPES,
                    provider="REDSHIFT",
                    table=f"{schema}.{mapping['table_name']}",
                )
            db.commit()
        finally:
            cursor.close()


async def prepare_destination(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    *,
    auto_create: bool,
) -> None:
    await asyncio.to_thread(_prepare_sync, connection, credentials, delivery, auto_create)


def _s3_client(connection: Connection, credentials: dict[str, str]):
    import boto3

    return boto3.client(
        "s3",
        region_name=connection.config_json["region"],
        **_aws_credentials(credentials),
    )


def _stage_sync(
    connection: Connection,
    credentials: dict[str, str],
    key: str,
    body: bytes,
    manifest: dict,
) -> dict:
    config = connection.config_json
    bucket = str(config["s3_bucket"])
    data_uri = f"s3://{bucket}/{key}"
    manifest_key = key + ".redshift-manifest.json"
    redshift_manifest = {"entries": [{"url": data_uri, "mandatory": True}]}
    result = {
        **manifest,
        "sha256": hashlib.sha256(body).hexdigest(),
        "data_object_uri": data_uri,
        "data_uri": f"s3://{bucket}/{manifest_key}",
    }
    client = _s3_client(connection, credentials)
    client.put_object(Bucket=bucket, Key=key, Body=body)
    client.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(redshift_manifest, separators=(",", ":")).encode(),
    )
    client.put_object(
        Bucket=bucket,
        Key=key + ".cluecdc-manifest.json",
        Body=json.dumps(result, sort_keys=True, separators=(",", ":")).encode(),
    )
    return result


def _copy_auth(connection: Connection, credentials: dict[str, str]) -> str:
    config = connection.config_json
    if config.get("iam_role_arn"):
        return f"IAM_ROLE {_literal(str(config['iam_role_arn']))}"
    if credentials.get("aws_access_key_id") and credentials.get("aws_secret_access_key"):
        value = (
            f"ACCESS_KEY_ID {_literal(credentials['aws_access_key_id'])} "
            f"SECRET_ACCESS_KEY {_literal(credentials['aws_secret_access_key'])}"
        )
        if credentials.get("aws_session_token"):
            value += f" SESSION_TOKEN {_literal(credentials['aws_session_token'])}"
        return value
    return "IAM_ROLE default"


def _marker_exists_sync(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    batch_id: str,
) -> bool:
    import redshift_connector

    schema = str(connection.config_json["schema_name"])
    _, marker = _names(delivery)
    with closing(redshift_connector.connect(**connection_options(connection, credentials))) as db:
        cursor = db.cursor()
        try:
            cursor.execute(
                f"SELECT COUNT(*) FROM {_quote(schema)}.{_quote(marker)} "
                "WHERE delivery_id=%s AND batch_id=%s",
                (str(delivery.id), batch_id),
            )
            return int(cursor.fetchone()[0]) > 0
        finally:
            cursor.close()


def _apply_sync(
    connection: Connection,
    credentials: dict[str, str],
    delivery: PipelineDestination,
    batch_id: str,
    manifest_uri: str,
) -> None:
    import redshift_connector

    config = connection.config_json
    schema = str(config["schema_name"])
    staging, marker = _names(delivery)
    with closing(redshift_connector.connect(**connection_options(connection, credentials))) as db:
        cursor = db.cursor()
        try:
            cursor.execute(f"TRUNCATE TABLE {_quote(schema)}.{_quote(staging)}")
            cursor.execute(
                f"COPY {_quote(schema)}.{_quote(staging)} FROM {_literal(manifest_uri)} "
                f"{_copy_auth(connection, credentials)} MANIFEST CSV EMPTYASNULL "
                f"REGION {_literal(str(config['region']))} COMPUPDATE OFF"
            )
            db.commit()
            cursor.execute(
                f"SELECT COUNT(*) FROM {_quote(schema)}.{_quote(marker)} "
                "WHERE delivery_id=%s AND batch_id=%s",
                (str(delivery.id), batch_id),
            )
            if int(cursor.fetchone()[0]) > 0:
                return
            target_columns = ", ".join(_quote(column) for column in HISTORY_COLUMNS)
            selected = ", ".join(f"s.{_quote(column)}" for column in HISTORY_COLUMNS)
            for mapping in delivery.topic_mapping_json:
                target = f"{_quote(schema)}.{_quote(mapping['table_name'])}"
                cursor.execute(
                    f"INSERT INTO {target} ({target_columns}) SELECT {selected} FROM "
                    f'{_quote(schema)}.{_quote(staging)} s WHERE s."_clue_topic"=%s '
                    f"AND NOT EXISTS (SELECT 1 FROM {target} t WHERE "
                    't."_clue_topic"=s."_clue_topic" AND '
                    't."_clue_partition"=s."_clue_partition" AND '
                    't."_clue_offset"=s."_clue_offset")',
                    (mapping["topic"],),
                )
            cursor.execute(
                f"INSERT INTO {_quote(schema)}.{_quote(marker)} "
                "(delivery_id,batch_id,applied_at) VALUES (%s,%s,GETDATE())",
                (str(delivery.id), batch_id),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            cursor.close()


def classify_error(error: Exception) -> dict:
    text = str(error).lower()
    if any(value in text for value in ("timeout", "temporarily", "connection reset", "throttl")):
        return {"code": "REDSHIFT_TRANSIENT", "retryable": True}
    if any(value in text for value in ("401", "403", "permission", "access denied")):
        return {"code": "REDSHIFT_PERMISSION_DENIED", "retryable": False}
    return {"code": "REDSHIFT_APPLY_FAILED", "retryable": False}


async def process_once(
    session,
    delivery: PipelineDestination,
    destination: Connection,
    credentials: dict[str, str],
    kafka: KafkaCluster,
    kafka_credentials: dict[str, str],
) -> dict:
    async def stage(key: str, body: bytes, manifest: dict) -> dict:
        return await asyncio.to_thread(_stage_sync, destination, credentials, key, body, manifest)

    async def apply(batch_id: str, uri: str) -> None:
        await asyncio.to_thread(_apply_sync, destination, credentials, delivery, batch_id, uri)

    async def marker(batch_id: str) -> bool:
        return await asyncio.to_thread(
            _marker_exists_sync, destination, credentials, delivery, batch_id
        )

    return await process_staged_once(
        session,
        delivery,
        kafka,
        kafka_credentials,
        provider="REDSHIFT",
        staging_prefix=str(destination.config_json["staging_prefix"]),
        stage=stage,
        apply=apply,
        marker_exists=marker,
        classify=classify_error,
    )
