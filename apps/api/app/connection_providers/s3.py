import asyncio
import os
import tempfile
import uuid
from typing import Any

from app.core.errors import DomainError
from app.models.entities import Connection


class S3ObjectStorageProvider:
    def __init__(self, connection: Connection, credentials: dict[str, str]):
        self.connection = connection
        self.config = connection.config_json
        self.credentials = credentials

    def _client(self):
        try:
            import boto3  # type: ignore[import-not-found]
            from botocore.config import Config  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - deployment packaging guard
            raise DomainError(
                "S3_CLIENT_UNAVAILABLE", "The API image does not include S3 client support", 503
            ) from exc
        ca_path = None
        custom_ca = self.config.get("custom_ca")
        if custom_ca:
            handle = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
            handle.write(str(custom_ca))
            handle.close()
            ca_path = handle.name
        verify: bool | str = ca_path or bool(self.config.get("ssl_enabled", True))
        client = boto3.client(
            "s3",
            endpoint_url=self.config.get("endpoint") or None,
            region_name=self.config["region"],
            aws_access_key_id=self.credentials.get("access_key") or None,
            aws_secret_access_key=self.credentials.get("secret_key") or None,
            aws_session_token=self.credentials.get("session_token") or None,
            verify=verify,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if self.config["path_style_access"] else "virtual"},
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        return client, ca_path

    def _test_sync(self) -> dict:
        client, ca_path = self._client()
        bucket = self.config["bucket"]
        prefix = str(self.config.get("base_path", "")).strip("/")
        key = "/".join(part for part in (prefix, ".cluecdc", f"test-{uuid.uuid4().hex}") if part)
        checks: list[dict[str, Any]] = []
        wrote = False
        try:
            client.head_bucket(Bucket=bucket)
            checks.extend(
                [
                    {"name": "endpoint", "status": "success"},
                    {"name": "authentication", "status": "success"},
                    {"name": "bucket_access", "status": "success"},
                ]
            )
            client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
            checks.append({"name": "base_path_access", "status": "success"})
            if self.config.get("verify_write", True):
                try:
                    client.put_object(Bucket=bucket, Key=key, Body=b"cluecdc-connection-test")
                    wrote = True
                    client.head_object(Bucket=bucket, Key=key)
                    checks.append({"name": "write_permission", "status": "success"})
                finally:
                    if wrote:
                        client.delete_object(Bucket=bucket, Key=key)
            else:
                checks.append(
                    {
                        "name": "write_permission",
                        "status": "skipped",
                        "message": "Write test disabled",
                    }
                )
        except Exception as exc:
            # boto errors can include signed request material; return a stable classification only.
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if str(code) in {"403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}:
                name, message = "authentication", "Storage rejected the configured credentials"
            elif str(code) in {"404", "NoSuchBucket"}:
                name, message = "bucket_access", "Configured bucket does not exist"
            else:
                name, message = (
                    "endpoint",
                    "Storage endpoint is unavailable or rejected the request",
                )
            checks.append({"name": name, "status": "failed", "message": message})
            raise DomainError(
                "OBJECT_STORAGE_TEST_FAILED", message, 422, {"checks": checks}
            ) from exc
        finally:
            client.close()
            if ca_path:
                try:
                    os.unlink(ca_path)
                except OSError:
                    pass
        return {"success": True, "status": "HEALTHY", "checks": checks}

    async def test_connection(self) -> dict:
        return await asyncio.to_thread(self._test_sync)

    def iceberg_properties(self, prefix: str = "iceberg.catalog.") -> dict[str, str]:
        properties = {
            prefix + "io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
            prefix + "client.region": str(self.config["region"]),
            prefix + "s3.path-style-access": str(self.config["path_style_access"]).lower(),
        }
        if self.config.get("endpoint"):
            properties[prefix + "s3.endpoint"] = str(self.config["endpoint"])
        return properties
