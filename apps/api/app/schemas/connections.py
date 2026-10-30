import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, model_validator


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    category: Literal["DATABASE", "OBJECT_STORAGE"] = "DATABASE"
    provider: Literal[
        "POSTGRESQL",
        "MYSQL",
        "AWS_S3",
        "MINIO",
        "AZURE_SYNAPSE",
        "CLICKHOUSE",
        "BIGQUERY",
        "REDSHIFT",
    ]
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
            "AZURE_SYNAPSE": "DATABASE",
            "CLICKHOUSE": "DATABASE",
            "BIGQUERY": "DATABASE",
            "REDSHIFT": "DATABASE",
        }[self.provider]
        if self.category != expected_category:
            raise ValueError(f"{self.provider} connections must use {expected_category}")
        if self.category == "OBJECT_STORAGE":
            return self._validate_object_storage()
        if self.provider == "AZURE_SYNAPSE":
            return self._validate_azure_synapse()
        if self.provider == "CLICKHOUSE":
            return self._validate_clickhouse()
        if self.provider == "BIGQUERY":
            return self._validate_bigquery()
        if self.provider == "REDSHIFT":
            return self._validate_redshift()
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

    def _destination_only(self, allowed_secrets: set[str]) -> None:
        if self.capabilities is not None and self.capabilities != ["DESTINATION"]:
            raise ValueError(f"{self.provider} supports the DESTINATION capability only")
        self.capabilities = ["DESTINATION"]
        unknown = set(self.credentials) - allowed_secrets
        if unknown:
            raise ValueError("Unsupported credential fields: " + ", ".join(sorted(unknown)))

    def _validate_clickhouse(self) -> "ConnectionInput":
        allowed = {"host", "port", "database_name", "username", "ssl_enabled", "cloud"}
        unknown = set(self.config) - allowed
        if unknown:
            raise ValueError("Unsupported config fields: " + ", ".join(sorted(unknown)))
        for key in ("host", "database_name", "username"):
            if not str(self.config.get(key, "")).strip():
                raise ValueError(f"{key} is required")
        port = self.config.get("port", 8443 if self.config.get("ssl_enabled", True) else 8123)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        host = str(self.config["host"]).strip().lower()
        if "://" in host or "/" in host or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", host):
            raise ValueError("host must be a DNS name or IP address without a URL scheme")
        self.config.update(host=host, port=port)
        self.config.setdefault("ssl_enabled", True)
        self.config.setdefault("cloud", False)
        if not all(isinstance(self.config[key], bool) for key in ("ssl_enabled", "cloud")):
            raise ValueError("TLS and cloud options must be boolean")
        if self.config["cloud"] and not self.config["ssl_enabled"]:
            raise ValueError("ClickHouse Cloud requires TLS")
        self._destination_only({"password"})
        return self

    def _validate_bigquery(self) -> "ConnectionInput":
        allowed = {
            "project_id",
            "dataset_id",
            "dataset_location",
            "authentication",
            "staging_bucket",
            "staging_prefix",
        }
        unknown = set(self.config) - allowed
        if unknown:
            raise ValueError("Unsupported config fields: " + ", ".join(sorted(unknown)))
        project = str(self.config.get("project_id", "")).strip()
        dataset = str(self.config.get("dataset_id", "")).strip()
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
            raise ValueError("project_id is not a valid Google Cloud project ID")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,1023}", dataset):
            raise ValueError("dataset_id is not a valid BigQuery dataset ID")
        authentication = self.config.get("authentication", "SERVICE_ACCOUNT")
        if authentication not in {"SERVICE_ACCOUNT", "ADC"}:
            raise ValueError("authentication must be SERVICE_ACCOUNT or ADC")
        location = str(self.config.get("dataset_location", "")).strip().upper()
        if not location:
            raise ValueError("dataset_location is required")
        prefix = str(self.config.get("staging_prefix", "cluecdc")).strip().strip("/")
        if ".." in prefix.split("/") or any(ord(char) < 32 for char in prefix):
            raise ValueError("staging_prefix contains an unsafe path segment")
        self.config.update(
            project_id=project,
            dataset_id=dataset,
            dataset_location=location,
            authentication=authentication,
            staging_prefix=prefix,
        )
        self._destination_only(
            {"service_account_json"} if authentication == "SERVICE_ACCOUNT" else set()
        )
        return self

    def _validate_redshift(self) -> "ConnectionInput":
        allowed = {
            "host",
            "port",
            "database_name",
            "schema_name",
            "username",
            "authentication",
            "region",
            "s3_bucket",
            "staging_prefix",
            "iam_role_arn",
            "ssl_enabled",
            "serverless",
            "cluster_identifier",
            "workgroup_name",
        }
        unknown = set(self.config) - allowed
        if unknown:
            raise ValueError("Unsupported config fields: " + ", ".join(sorted(unknown)))
        for key in ("host", "database_name", "schema_name", "username", "region", "s3_bucket"):
            if not str(self.config.get(key, "")).strip():
                raise ValueError(f"{key} is required")
        port = self.config.get("port", 5439)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        authentication = self.config.get("authentication", "PASSWORD")
        if authentication not in {"PASSWORD", "IAM"}:
            raise ValueError("authentication must be PASSWORD or IAM")
        if authentication == "IAM":
            identity = (
                self.config.get("workgroup_name")
                if self.config.get("serverless", False)
                else self.config.get("cluster_identifier")
            )
            if not str(identity or "").strip():
                field = (
                    "workgroup_name"
                    if self.config.get("serverless", False)
                    else "cluster_identifier"
                )
                raise ValueError(f"{field} is required for Redshift IAM authentication")
        prefix = str(self.config.get("staging_prefix", "cluecdc")).strip().strip("/")
        if not prefix or ".." in prefix.split("/") or any(ord(char) < 32 for char in prefix):
            raise ValueError("staging_prefix must be a safe non-empty relative prefix")
        self.config.update(port=port, authentication=authentication, staging_prefix=prefix)
        self.config.setdefault("ssl_enabled", True)
        self.config.setdefault("serverless", False)
        if self.config["ssl_enabled"] is not True:
            raise ValueError("Amazon Redshift connections require TLS")
        secrets = {"password"} if authentication == "PASSWORD" else set()
        secrets.update({"aws_access_key_id", "aws_secret_access_key", "aws_session_token"})
        self._destination_only(secrets)
        return self

    def _validate_azure_synapse(self) -> "ConnectionInput":
        allowed = {
            "workspace_name",
            "sql_endpoint",
            "port",
            "database_name",
            "target_schema",
            "authentication",
            "username",
            "tenant_id",
            "client_id",
            "managed_identity_client_id",
            "ssl_enabled",
            "storage_account",
            "storage_type",
            "container",
            "staging_prefix",
            "storage_authentication",
            "staging_retention_days",
            "batch_size",
            "batch_flush_interval_ms",
            "maximum_concurrency",
            "max_retries",
            "retry_backoff_ms",
            "statement_timeout_seconds",
            "distribution_strategy",
        }
        unknown = set(self.config) - allowed
        if unknown:
            raise ValueError("Unsupported config fields: " + ", ".join(sorted(unknown)))
        required = (
            "workspace_name",
            "sql_endpoint",
            "database_name",
            "target_schema",
            "storage_account",
            "container",
        )
        for key in required:
            if not str(self.config.get(key, "")).strip():
                raise ValueError(f"{key} is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,49}", str(self.config["workspace_name"])):
            raise ValueError("workspace_name contains invalid characters")
        if not re.fullmatch(r"[a-z0-9]{3,24}", str(self.config["storage_account"])):
            raise ValueError("storage_account must be 3-24 lowercase letters or digits")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", str(self.config["container"])):
            raise ValueError("container must be a valid Azure Storage container name")
        endpoint = str(self.config["sql_endpoint"]).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.sql\.azuresynapse\.net", endpoint):
            raise ValueError("sql_endpoint must be an Azure Synapse SQL endpoint")
        if "-ondemand.sql.azuresynapse.net" in endpoint:
            raise ValueError("Serverless SQL Pool is not a stateful CDC destination")
        self.config["sql_endpoint"] = endpoint
        port = self.config.get("port", 1433)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.config.get("ssl_enabled", True) is not True:
            raise ValueError("Azure Synapse connections require TLS")
        authentication = self.config.get("authentication", "SQL_PASSWORD")
        if authentication not in {"SQL_PASSWORD", "SERVICE_PRINCIPAL", "MANAGED_IDENTITY"}:
            raise ValueError("Unsupported Azure Synapse authentication method")
        if authentication == "SQL_PASSWORD":
            if not str(self.config.get("username", "")).strip():
                raise ValueError("username is required for SQL password authentication")
            if self.config.get("client_id") or self.config.get("tenant_id"):
                raise ValueError(
                    "Tenant ID and client ID apply only to Microsoft Entra authentication"
                )
        elif authentication == "SERVICE_PRINCIPAL":
            if self.config.get("managed_identity_client_id"):
                raise ValueError(
                    "Managed identity client ID applies only to managed-identity authentication"
                )
            for key in ("tenant_id", "client_id"):
                if not re.fullmatch(
                    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                    str(self.config.get(key, "")),
                ):
                    raise ValueError(f"{key} must be a UUID")
        else:
            from app.core.config import get_settings

            if not get_settings().azure_managed_identity_enabled:
                raise ValueError("Managed identity is not enabled for this ClueCDC deployment")
            if self.config.get("tenant_id") or self.config.get("client_id"):
                raise ValueError(
                    "Tenant ID and service-principal client ID do not apply to managed identity"
                )
        storage_auth = self.config.get("storage_authentication", "ACCOUNT_KEY")
        if storage_auth not in {
            "ACCOUNT_KEY",
            "SAS_TOKEN",
            "SERVICE_PRINCIPAL",
            "MANAGED_IDENTITY",
        }:
            raise ValueError("Unsupported Azure Storage authentication method")
        if storage_auth == "SERVICE_PRINCIPAL" and authentication != "SERVICE_PRINCIPAL":
            raise ValueError(
                "Storage service principal requires Entra service-principal authentication"
            )
        if storage_auth == "MANAGED_IDENTITY" and authentication != "MANAGED_IDENTITY":
            raise ValueError("Storage managed identity requires managed-identity authentication")
        prefix = str(self.config.get("staging_prefix", "cluecdc")).strip().strip("/")
        if not prefix or ".." in prefix.split("/") or any(ord(char) < 32 for char in prefix):
            raise ValueError("staging_prefix must be a safe non-empty relative prefix")
        numeric_ranges = {
            "staging_retention_days": (1, 365),
            "batch_size": (1, 1000000),
            "batch_flush_interval_ms": (1000, 3600000),
            "maximum_concurrency": (1, 32),
            "max_retries": (0, 20),
            "retry_backoff_ms": (100, 60000),
            "statement_timeout_seconds": (1, 86400),
        }
        defaults = {
            "staging_retention_days": 7,
            "batch_size": 10000,
            "batch_flush_interval_ms": 30000,
            "maximum_concurrency": 2,
            "max_retries": 5,
            "retry_backoff_ms": 1000,
            "statement_timeout_seconds": 900,
        }
        for key, (minimum, maximum) in numeric_ranges.items():
            value = self.config.get(key, defaults[key])
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
            self.config[key] = value
        distribution = self.config.get("distribution_strategy", "ROUND_ROBIN")
        if distribution not in {"ROUND_ROBIN", "HASH", "REPLICATE"}:
            raise ValueError("Unsupported Synapse table distribution strategy")
        self.config.update(
            port=port,
            ssl_enabled=True,
            authentication=authentication,
            storage_type=self.config.get("storage_type", "ADLS_GEN2"),
            storage_authentication=storage_auth,
            staging_prefix=prefix,
            distribution_strategy=distribution,
        )
        if self.config["storage_type"] not in {"ADLS_GEN2", "BLOB"}:
            raise ValueError("storage_type must be ADLS_GEN2 or BLOB")
        if self.capabilities is not None and self.capabilities != ["DESTINATION"]:
            raise ValueError("Azure Synapse supports the DESTINATION capability only")
        self.capabilities = ["DESTINATION"]
        allowed_secrets = {"password" if authentication == "SQL_PASSWORD" else "client_secret"}
        if storage_auth == "ACCOUNT_KEY":
            allowed_secrets.add("storage_account_key")
        elif storage_auth == "SAS_TOKEN":
            allowed_secrets.add("storage_sas_token")
        unknown_secrets = set(self.credentials) - allowed_secrets
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
