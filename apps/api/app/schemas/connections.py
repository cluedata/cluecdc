import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, model_validator


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    category: Literal["DATABASE", "OBJECT_STORAGE"] = "DATABASE"
    provider: Literal["POSTGRESQL", "MYSQL", "AWS_S3", "MINIO"]
    config: dict
    credentials: dict[str, SecretStr] = Field(default_factory=dict)
    capabilities: list[Literal["SOURCE", "DESTINATION"]] | None = None

    @model_validator(mode="after")
    def validate_connection(self) -> "ConnectionInput":
        expected_category = {
            "POSTGRESQL": "DATABASE",
            "MYSQL": "DATABASE",
            "AWS_S3": "OBJECT_STORAGE",
            "MINIO": "OBJECT_STORAGE",
        }[self.provider]
        if self.category != expected_category:
            raise ValueError(f"{self.provider} connections must use {expected_category}")
        if self.category == "OBJECT_STORAGE":
            return self._validate_object_storage()
        allowed = {
            "host",
            "port",
            "database_name",
            "username",
            "ssl_enabled",
            "environment",
            "provider_options",
        }
        unknown = set(self.config) - allowed
        if unknown:
            raise ValueError("Unsupported config fields: " + ", ".join(sorted(unknown)))
        for key in ("host", "database_name", "username"):
            if not str(self.config.get(key, "")).strip():
                raise ValueError(f"{key} is required")
        defaults = {"POSTGRESQL": 5432, "MYSQL": 3306}
        port = self.config.get("port", defaults[self.provider])
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.config.setdefault("port", port)
        self.config.setdefault("ssl_enabled", False)
        self.config.setdefault("environment", "DEV")
        self.config.setdefault("provider_options", {})
        from app.schemas.requests import SourceInput

        SourceInput(name=self.name, type=self.provider.lower(), **self.config)
        options = self.config["provider_options"]
        if not isinstance(options, dict) or any(
            not isinstance(key, str)
            or any(
                word in key.lower()
                for word in ("password", "secret", "token", "credential", "access_key")
            )
            for key in options
        ):
            raise ValueError("provider_options cannot contain credential fields")
        if self.capabilities is None:
            self.capabilities = ["SOURCE", "DESTINATION"]
        if not self.capabilities:
            raise ValueError("A database connection needs at least one capability")
        unknown_secrets = set(self.credentials) - {"password"}
        if unknown_secrets:
            raise ValueError("Unsupported credential fields: " + ", ".join(sorted(unknown_secrets)))
        return self

    def _validate_object_storage(self) -> "ConnectionInput":
        allowed = {
            "region",
            "bucket",
            "prefix",
            "endpoint",
            "use_ssl",
            "path_style_access",
            "tls_verify",
            "connection_timeout_seconds",
        }
        unknown = set(self.config) - allowed
        if unknown:
            raise ValueError("Unsupported config fields: " + ", ".join(sorted(unknown)))
        bucket = str(self.config.get("bucket", "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ValueError("bucket must be a valid S3 bucket name")
        endpoint = str(self.config.get("endpoint", "")).strip()
        if self.provider == "MINIO" and not endpoint:
            raise ValueError("endpoint is required for MinIO")
        if endpoint:
            parsed = urlparse(endpoint)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("endpoint must be an HTTP(S) origin without credentials or a path")
            self.config["endpoint"] = endpoint.rstrip("/")
            if bool(self.config.get("use_ssl", parsed.scheme == "https")) != (
                parsed.scheme == "https"
            ):
                raise ValueError("use_ssl must match the endpoint scheme")
        prefix = str(self.config.get("prefix", "")).strip().strip("/")
        if prefix and (".." in prefix.split("/") or any(ord(char) < 32 for char in prefix)):
            raise ValueError("prefix contains an unsafe path segment")
        timeout = self.config.get("connection_timeout_seconds", 10)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
            raise ValueError("connection_timeout_seconds must be between 1 and 120")
        self.config.setdefault("region", "us-east-1")
        self.config["prefix"] = prefix
        self.config.setdefault("use_ssl", not endpoint.startswith("http://") if endpoint else True)
        self.config.setdefault("path_style_access", self.provider == "MINIO")
        self.config.setdefault("tls_verify", True)
        if any(
            not isinstance(self.config[key], bool)
            for key in ("use_ssl", "path_style_access", "tls_verify")
        ):
            raise ValueError("TLS and addressing options must be boolean")
        if endpoint and not self.config["path_style_access"]:
            raise ValueError(
                "The selected connector requires path-style access for endpoint overrides"
            )
        if not endpoint and self.config["use_ssl"] is not True:
            raise ValueError("AWS S3 requires TLS without an endpoint override")
        if self.config["tls_verify"] is not True:
            raise ValueError("TLS verification cannot be disabled")
        self.config["connection_timeout_seconds"] = timeout
        if self.capabilities is not None and self.capabilities != ["DESTINATION"]:
            raise ValueError("Object storage supports the DESTINATION capability only")
        self.capabilities = ["DESTINATION"]
        allowed_secrets = {"access_key", "secret_key"}
        if self.provider == "AWS_S3":
            allowed_secrets.add("session_token")
        unknown_secrets = set(self.credentials) - allowed_secrets
        if unknown_secrets:
            raise ValueError("Unsupported credential fields: " + ", ".join(sorted(unknown_secrets)))
        return self
