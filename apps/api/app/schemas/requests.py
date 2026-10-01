import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

DATABASE_TYPES = Literal["postgresql", "mysql"]


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: DATABASE_TYPES = "postgresql"
    environment: str = Field(default="development", min_length=1, max_length=30)
    host: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_.:\-]+$")
    port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr | None = None
    ssl_enabled: bool = False
    provider_options: dict[str, str | int | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider_options(self) -> "SourceInput":
        allowed = (
            {"connection_timeout_seconds", "ssl_mode"}
            if self.type == "postgresql"
            else {"server_id", "connection_timeout_seconds", "ssl_mode"}
        )
        unknown = set(self.provider_options) - allowed
        if unknown:
            raise ValueError(f"Unsupported {self.type} options: {', '.join(sorted(unknown))}")
        server_id = self.provider_options.get("server_id")
        if server_id is not None and (
            not isinstance(server_id, int)
            or isinstance(server_id, bool)
            or not 1 <= server_id <= 4294967295
        ):
            raise ValueError("MySQL server_id must be an integer between 1 and 4294967295")
        timeout = self.provider_options.get("connection_timeout_seconds", 10)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
            raise ValueError("Connection timeout must be between 1 and 120 seconds")
        return self


class KafkaInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    bootstrap_servers: str = Field(min_length=1, max_length=1000)
    security_protocol: Literal["PLAINTEXT", "SSL"] = "PLAINTEXT"

    @field_validator("bootstrap_servers")
    @classmethod
    def validate_servers(cls, value: str) -> str:
        if not all(re.fullmatch(r"[a-zA-Z0-9_.-]+:\d{1,5}", s) for s in value.split(",")):
            raise ValueError("Use comma-separated hostname:port addresses")
        return value


class ConnectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=1000)
    kafka_cluster_id: UUID

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("Use an HTTP(S) endpoint without embedded credentials")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("Use the Kafka Connect origin without a path or query")
        return value.rstrip("/")


class TableSelection(BaseModel):
    schema_name: str = Field(min_length=1, max_length=128)
    table_name: str = Field(min_length=1, max_length=128)


class AddPipelineTableInput(TableSelection):
    initial_data_strategy: Literal["BACKFILL", "FUTURE_ONLY"] = "BACKFILL"
    destination_handling: Literal["AUTO_CREATE", "USE_EXISTING", "VALIDATE_ONLY"] = "AUTO_CREATE"
    destination_schema: str | None = Field(
        default=None, max_length=63, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    destination_table: str | None = Field(
        default=None, max_length=63, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    custom_key_columns: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("custom_key_columns")
    @classmethod
    def validate_custom_keys(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", column) for column in value
        ):
            raise ValueError("Custom key columns must be unique PostgreSQL identifiers")
        return value


class AddPipelineTablesInput(BaseModel):
    tables: list[AddPipelineTableInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_tables(self) -> "AddPipelineTablesInput":
        keys = [(table.schema_name, table.table_name) for table in self.tables]
        if len(keys) != len(set(keys)):
            raise ValueError("Tables must be unique")
        return self


class RemovePipelineTableInput(BaseModel):
    destination_handling: Literal["KEEP_DATA", "DELETE_TABLE"] = "KEEP_DATA"
    confirm_destination_delete: bool = False

    @model_validator(mode="after")
    def confirm_delete(self) -> "RemovePipelineTableInput":
        if self.destination_handling == "DELETE_TABLE" and not self.confirm_destination_delete:
            raise ValueError("Deleting destination tables requires explicit confirmation")
        return self


class ResyncPipelineTableInput(BaseModel):
    scope: Literal["ENTIRE_TABLE"] = "ENTIRE_TABLE"


class PipelineInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_id: UUID
    kafka_cluster_id: UUID
    connect_cluster_id: UUID
    topic_prefix: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    snapshot_mode: Literal["initial", "no_data", "never", "always"] = "initial"
    tables: list[TableSelection] = Field(min_length=1, max_length=500)
    heartbeat_interval_ms: int = Field(default=10000, ge=0, le=3600000)
    max_batch_size: int = Field(default=2048, ge=1, le=100000)
    max_queue_size: int = Field(default=8192, ge=2, le=1000000)
    poll_interval_ms: int = Field(default=500, ge=100, le=60000)
    additional_debezium_properties: dict[str, str] = Field(default_factory=dict, max_length=50)
    provider_options: dict[str, str | int | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_queue(self) -> "PipelineInput":
        if self.max_queue_size <= self.max_batch_size:
            raise ValueError("Queue size must exceed batch size")
        keys = [(t.schema_name, t.table_name) for t in self.tables]
        if len(set(keys)) != len(keys):
            raise ValueError("Tables must be unique")
        protected = {
            "name",
            "connector.class",
            "topic.prefix",
            "database.hostname",
            "database.port",
            "database.user",
            "database.password",
            "database.server.id",
            "database.include.list",
            "table.include.list",
            "slot.name",
            "publication.name",
            "schema.history.internal.kafka.bootstrap.servers",
            "schema.history.internal.kafka.topic",
            "signal.data.collection",
        }
        denied = protected & set(self.additional_debezium_properties)
        if denied:
            raise ValueError(
                "ClueCDC-managed Debezium properties cannot be overridden: "
                + ", ".join(sorted(denied))
            )
        return self


class DestinationInput(SourceInput):
    environment: Literal["DEV", "STAGING", "PROD"] = "DEV"
    description: str = Field(default="", max_length=1000)


class TopicMapping(BaseModel):
    topic: str = Field(min_length=1, max_length=249, pattern=r"^[a-zA-Z0-9_.-]+$")
    schema_name: str = Field(default="public", max_length=63, pattern=r"^[a-z_][a-z0-9_]*$")
    table_name: str = Field(min_length=1, max_length=63, pattern=r"^[a-z_][a-z0-9_]*$")


class DeliveryInput(BaseModel):
    pipeline_id: UUID
    connect_cluster_id: UUID | None = None
    name: str = Field(default="Primary delivery", min_length=1, max_length=120)
    mappings: list[TopicMapping] = Field(min_length=1, max_length=100)
    write_mode: Literal["upsert", "insert"] = "upsert"
    primary_key_mode: Literal["record_key"] = "record_key"
    auto_create: bool = True
    auto_evolve: bool = True
    delete_enabled: bool = True
    null_handling: Literal["ignore"] = "ignore"
    batch_size: int = Field(default=500, ge=1, le=10000)
    max_retries: int = Field(default=5, ge=0, le=20)
    retry_backoff_ms: int = Field(default=1000, ge=100, le=10000)
    tasks_max: int = Field(default=1, ge=1, le=4)

    @model_validator(mode="after")
    def valid_delivery(self) -> "DeliveryInput":
        if self.auto_evolve and not self.auto_create:
            raise ValueError("Auto evolve requires auto create for the installed JDBC plugin")
        topics = [mapping.topic for mapping in self.mappings]
        targets = [(mapping.schema_name, mapping.table_name) for mapping in self.mappings]
        if len(set(topics)) != len(topics) or len(set(targets)) != len(targets):
            raise ValueError("Each topic and destination table must occur once per delivery")
        if self.max_retries * self.retry_backoff_ms >= 240000:
            raise ValueError("Total retry delay must stay below 240 seconds")
        return self
