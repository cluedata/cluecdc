import asyncio
import logging
import uuid
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.errors import DomainError
from app.models.entities import Connection

for logger_name in ("boto3", "botocore", "s3transfer"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)


def _client(connection: Connection, credentials: dict[str, str]):
    config = connection.config_json
    timeout = int(config.get("connection_timeout_seconds", 10))
    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "region_name": str(config.get("region", "us-east-1")),
        "aws_access_key_id": credentials["access_key"],
        "aws_secret_access_key": credentials["secret_key"],
        "use_ssl": bool(config.get("use_ssl", True)),
        "verify": bool(config.get("tls_verify", True)),
        "config": Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 2, "mode": "standard"},
            s3={
                "addressing_style": (
                    "path" if config.get("path_style_access", False) else "virtual"
                )
            },
        ),
    }
    if config.get("endpoint"):
        kwargs["endpoint_url"] = config["endpoint"]
    if credentials.get("session_token"):
        kwargs["aws_session_token"] = credentials["session_token"]
    return boto3.client(**kwargs)


def _test_sync(connection: Connection, credentials: dict[str, str]) -> dict:
    try:
        client = _client(connection, credentials)
    except (BotoCoreError, ValueError, OSError, KeyError, TypeError):
        raise DomainError(
            "OBJECT_STORAGE_CONFIG_INVALID", "Object storage client configuration is invalid", 422
        ) from None
    bucket = str(connection.config_json["bucket"])
    prefix = str(connection.config_json.get("prefix", "")).strip("/")
    test_key = "/".join(
        part
        for part in (prefix, ".cluecdc", "connection-tests", f"{uuid.uuid4().hex}.probe")
        if part
    )
    write_attempted = False
    try:
        client.head_bucket(Bucket=bucket)
        # A timed-out PUT may have succeeded remotely; always attempt cleanup.
        write_attempted = True
        client.put_object(
            Bucket=bucket,
            Key=test_key,
            Body=b"cluecdc connection test\n",
            ContentType="text/plain",
        )
        client.head_object(Bucket=bucket, Key=test_key)
        return {
            "success": True,
            "checks": [
                {"name": "bucket", "status": "success"},
                {"name": "write", "status": "success"},
                {"name": "cleanup", "status": "success"},
            ],
        }
    except (BotoCoreError, ClientError, OSError) as exc:
        code = "OBJECT_STORAGE_UNAVAILABLE"
        status = 503
        if isinstance(exc, ClientError):
            upstream_code = str(exc.response.get("Error", {}).get("Code", ""))
            if upstream_code in {
                "AccessDenied",
                "InvalidAccessKeyId",
                "InvalidToken",
                "SignatureDoesNotMatch",
                "ExpiredToken",
                "403",
            }:
                code, status = "OBJECT_STORAGE_AUTH_FAILED", 422
            elif upstream_code in {"NoSuchBucket", "NotFound", "404"}:
                code, status = "OBJECT_STORAGE_BUCKET_NOT_FOUND", 422
        raise DomainError(code, "Object storage connection test failed", status) from None
    finally:
        try:
            if write_attempted:
                client.delete_object(Bucket=bucket, Key=test_key)
        except (BotoCoreError, ClientError, OSError):
            raise DomainError(
                "OBJECT_STORAGE_TEST_CLEANUP_FAILED",
                "The temporary connection-test object could not be removed",
                503,
            ) from None
        finally:
            client.close()


async def test_connection(connection: Connection, credentials: dict[str, str]) -> dict:
    """Run the blocking AWS SDK outside the FastAPI event loop."""
    return await asyncio.to_thread(_test_sync, connection, credentials)
