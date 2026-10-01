import re
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

ConnectionCategory = Literal["DATABASE", "OBJECT_STORAGE"]
ConnectionProvider = Literal[
    "POSTGRESQL",
    "MYSQL",
    "SQL_SERVER",
    "ORACLE",
    "AWS_S3",
    "MINIO",
]

PROVIDER_CATEGORIES = {
    "POSTGRESQL": "DATABASE",
    "MYSQL": "DATABASE",
    "SQL_SERVER": "DATABASE",
    "ORACLE": "DATABASE",
    "AWS_S3": "OBJECT_STORAGE",
    "MINIO": "OBJECT_STORAGE",
}

PROVIDER_SECRET_KEYS = {
    "POSTGRESQL": {"password"},
    "MYSQL": {"password"},
    "SQL_SERVER": {"password"},
    "ORACLE": {"password"},
    "AWS_S3": {"access_key", "secret_key", "session_token"},
    "MINIO": {"access_key", "secret_key", "session_token"},
}


def _endpoint(value: str, field: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError(f"{field} must be an HTTP(S) URL without embedded credentials")
    if parsed.fragment:
        raise ValueError(f"{field} must not contain a fragment")
    return value.rstrip("/")


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: ConnectionCategory
    provider: ConnectionProvider
    description: str = Field(default="", max_length=1000)
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, SecretStr | None] = Field(default_factory=dict)
    capabilities: list[Literal["SOURCE", "DESTINATION"]] | None = None

    @model_validator(mode="after")
    def validate_provider(self) -> "ConnectionInput":
        if PROVIDER_CATEGORIES[self.provider] != self.category:
            raise ValueError("Provider does not belong to the selected connection category")
        allowed_capabilities = {
            "DATABASE": {"SOURCE", "DESTINATION"},
            "OBJECT_STORAGE": {"DESTINATION"},
        }[self.category]
        if self.capabilities is not None and not set(self.capabilities) <= allowed_capabilities:
            raise ValueError("Capabilities do not belong to the selected connection category")
        unknown_secrets = set(self.credentials) - PROVIDER_SECRET_KEYS[self.provider]
        if unknown_secrets:
            raise ValueError("Unsupported credential fields: " + ", ".join(sorted(unknown_secrets)))
        validators = {
            "POSTGRESQL": self._validate_database,
            "MYSQL": self._validate_database,
            "SQL_SERVER": self._validate_database,
            "ORACLE": self._validate_database,
            "AWS_S3": self._validate_s3,
            "MINIO": self._validate_s3,
        }
        validators[self.provider]()
        return self

    def _validate_database(self) -> None:
        allowed = {
            "host",
            "port",
            "database_name",
            "username",
            "ssl_enabled",
            "environment",
            "provider_options",
        }
        self._only(allowed)
        if not str(self.config.get("host", "")).strip():
            raise ValueError("host is required")
        if not str(self.config.get("database_name", "")).strip():
            raise ValueError("database_name is required")
        if not str(self.config.get("username", "")).strip():
            raise ValueError("username is required")
        defaults = {"POSTGRESQL": 5432, "MYSQL": 3306, "SQL_SERVER": 1433, "ORACLE": 1521}
        port = self.config.get("port", defaults[self.provider])
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.config.setdefault("port", port)
        self.config.setdefault("ssl_enabled", False)
        self.config.setdefault("environment", "DEV")
        self.config.setdefault("provider_options", {})
        if self.capabilities is None:
            self.capabilities = ["SOURCE", "DESTINATION"]
        if not self.capabilities:
            raise ValueError("A database connection needs at least one capability")

    def _validate_s3(self) -> None:
        allowed = {
            "endpoint",
            "region",
            "bucket",
            "base_path",
            "path_style_access",
            "ssl_enabled",
            "custom_ca",
            "verify_write",
        }
        self._only(allowed)
        for key in ("region", "bucket"):
            if not str(self.config.get(key, "")).strip():
                raise ValueError(f"{key} is required")
        endpoint = str(self.config.get("endpoint", "")).strip()
        if self.provider != "AWS_S3" and not endpoint:
            raise ValueError("endpoint is required for MinIO storage")
        if endpoint:
            self.config["endpoint"] = _endpoint(endpoint, "endpoint")
        self.config.setdefault("path_style_access", self.provider != "AWS_S3")
        self.config.setdefault("ssl_enabled", True)
        self.config.setdefault("base_path", "")
        if self.config["base_path"].startswith("/") or ".." in self.config["base_path"].split("/"):
            raise ValueError("base_path must be a relative object prefix")

    def _only(self, allowed: set[str]) -> None:
        unknown = set(self.config) - allowed
        if unknown:
            raise ValueError("Unsupported config fields: " + ", ".join(sorted(unknown)))


class LakehouseTargetInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    table_format: Literal["ICEBERG"] = "ICEBERG"
    storage_connection_id: UUID
    warehouse: str = Field(min_length=1, max_length=1000)
    namespace: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    file_format: Literal["PARQUET", "ORC"] = "PARQUET"
    write_mode: Literal["UPSERT", "APPEND_ONLY"] = "UPSERT"
    delete_mode: Literal["PROPAGATE", "IGNORE"] = "PROPAGATE"
    partition_config: list[str] = Field(default_factory=list, max_length=16)
    identifier_fields: list[str] = Field(default_factory=list, max_length=16)
    schema_evolution: bool = True
    auto_create_tables: bool = True

    @field_validator("partition_config", "identifier_fields")
    @classmethod
    def identifiers(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) for value in values
        ):
            raise ValueError("Values must be unique identifiers")
        return values

    @model_validator(mode="after")
    def cdc_semantics(self) -> "LakehouseTargetInput":
        if self.write_mode == "APPEND_ONLY" and self.delete_mode == "PROPAGATE":
            raise ValueError("Delete propagation requires UPSERT write mode")
        return self


# Backward-compatible request name for the deprecated /lakehouse-destinations routes.
LakehouseDestinationInput = LakehouseTargetInput


class IcebergDeliveryInput(BaseModel):
    pipeline_id: UUID
    connect_cluster_id: UUID | None = None
    name: str = Field(default="Iceberg delivery", min_length=1, max_length=120)
    commit_interval_ms: int = Field(default=60_000, ge=1_000, le=3_600_000)
    tasks_max: int = Field(default=1, ge=1, le=16)
    table_routing: dict[str, str] = Field(default_factory=dict, max_length=500)

    @field_validator("table_routing")
    @classmethod
    def routing(cls, value: dict[str, str]) -> dict[str, str]:
        topic = re.compile(r"^[A-Za-z0-9_.-]+$")
        table = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        if any(
            not topic.fullmatch(key) or not table.fullmatch(target) for key, target in value.items()
        ):
            raise ValueError("Routing must map Kafka topic names to valid Iceberg table names")
        return value
