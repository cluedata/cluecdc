import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Entity:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class SecretReference(Entity, Base):
    __tablename__ = "secret_references"
    ciphertext: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String, default="encrypted-database")


class SourceTable(Entity, Base):
    __tablename__ = "source_tables"
    __table_args__ = (UniqueConstraint("source_connection_id", "schema_name", "table_name"),)
    # The Python alias remains source_id until the v1 compatibility API is retired.
    source_id: Mapped[uuid.UUID] = mapped_column(
        "source_connection_id", ForeignKey("connections.id", ondelete="CASCADE")
    )
    schema_name: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(128))
    primary_key_columns: Mapped[list] = mapped_column(JSON, default=list)
    columns_json: Mapped[list] = mapped_column(JSON, default=list)
    indexes_json: Mapped[list] = mapped_column(JSON, default=list)
    estimated_rows: Mapped[int | None] = mapped_column(BigInteger)
    estimated_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    cdc_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    cdc_status: Mapped[str] = mapped_column(String, default="UNKNOWN")
    cdc_issues: Mapped[list] = mapped_column(JSON, default=list)


class KafkaCluster(Entity, Base):
    __tablename__ = "kafka_clusters"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    bootstrap_servers: Mapped[str] = mapped_column(String)
    security_protocol: Mapped[str] = mapped_column(String, default="PLAINTEXT")
    secret_ref: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secret_references.id"))
    status: Mapped[str] = mapped_column(String, default="UNKNOWN")


class ConnectCluster(Entity, Base):
    __tablename__ = "connect_clusters"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    base_url: Mapped[str] = mapped_column(String)
    kafka_cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kafka_clusters.id"), index=True)
    status: Mapped[str] = mapped_column(String, default="UNKNOWN")


class Connector(Entity, Base):
    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("connect_cluster_id", "name"),)
    name: Mapped[str] = mapped_column(String(120))
    connector_type: Mapped[str] = mapped_column(String, default="source")
    connect_cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connect_clusters.id"))
    connector_class: Mapped[str] = mapped_column(String)
    config_json: Mapped[dict] = mapped_column(JSON)
    desired_state: Mapped[str] = mapped_column(String, default="STOPPED")
    actual_state: Mapped[str] = mapped_column(String, default="UNKNOWN")
    runtime_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Pipeline(Entity, Base):
    __tablename__ = "pipelines"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        "source_connection_id", ForeignKey("connections.id"), index=True
    )
    kafka_cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kafka_clusters.id"))
    connect_cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connect_clusters.id"))
    connector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("connectors.id"))
    topic_prefix: Mapped[str] = mapped_column(String(120), unique=True)
    snapshot_mode: Mapped[str] = mapped_column(String)
    config_options: Mapped[dict] = mapped_column(JSON, default=dict)
    desired_state: Mapped[str] = mapped_column(String, default="STOPPED")
    actual_state: Mapped[str] = mapped_column(String, default="UNKNOWN", index=True)


class PipelineTable(Entity, Base):
    __tablename__ = "pipeline_tables"
    __table_args__ = (UniqueConstraint("pipeline_id", "schema_name", "table_name"),)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id", ondelete="CASCADE"))
    schema_name: Mapped[str] = mapped_column(String)
    table_name: Mapped[str] = mapped_column(String)
    topic_name: Mapped[str] = mapped_column(String)
    primary_key_columns: Mapped[list] = mapped_column(JSON, default=list)
    destination_schema: Mapped[str | None] = mapped_column(String(128))
    destination_table: Mapped[str | None] = mapped_column(String(128))
    initial_data_strategy: Mapped[str] = mapped_column(String, default="BACKFILL")
    delete_strategy: Mapped[str] = mapped_column(String, default="DELETE")
    snapshot_status: Mapped[str] = mapped_column(String, default="PENDING")
    cdc_status: Mapped[str] = mapped_column(String, default="PENDING")
    schema_status: Mapped[str] = mapped_column(String, default="IN_SYNC")
    destination_status: Mapped[str] = mapped_column(String, default="READY")
    snapshot_rows_processed: Mapped[int | None] = mapped_column(BigInteger)
    snapshot_estimated_rows: Mapped[int | None] = mapped_column(BigInteger)
    snapshot_current_chunk: Mapped[str | None] = mapped_column(String(255))
    snapshot_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipelineOperation(Entity, Base):
    __tablename__ = "pipeline_operations"
    __table_args__ = (Index("ix_pipeline_operations_pipeline_status", "pipeline_id", "status"),)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), index=True
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_tables.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    current_step: Mapped[str | None] = mapped_column(String(80))
    progress: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Connection(Entity, Base):
    """The only persisted representation of an external system."""

    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint(
            "category IN ('DATABASE','OBJECT_STORAGE')",
            name="ck_connections_category",
        ),
        CheckConstraint(
            "provider IN ('POSTGRESQL','MYSQL','AWS_S3','MINIO')",
            name="ck_connections_provider",
        ),
    )
    name: Mapped[str] = mapped_column(String(120), unique=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN", index=True)
    description: Mapped[str] = mapped_column(String(1000), default="")
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    capabilities_json: Mapped[list] = mapped_column(JSON, default=list)
    secret_ref: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secret_references.id", ondelete="RESTRICT")
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(20))
    last_test_message: Mapped[str | None] = mapped_column(String(1000))

    def __init__(self, **kwargs: Any):
        """Accept the old database endpoint shape only as an in-memory adapter.

        This keeps provider unit tests and v1 DTO construction simple without reviving
        the removed ``sources`` or ``destinations`` tables.
        """
        legacy_keys = {
            "type",
            "environment",
            "host",
            "port",
            "database_name",
            "username",
            "ssl_enabled",
            "provider_options",
            "last_health_check_at",
        }
        if legacy_keys & kwargs.keys():
            database_type = str(kwargs.pop("type", "postgresql"))
            kwargs.setdefault("category", "DATABASE")
            kwargs.setdefault("provider", database_type.upper())
            kwargs.setdefault("capabilities_json", ["SOURCE", "DESTINATION"])
            config = dict(kwargs.pop("config_json", {}))
            for key in legacy_keys - {"type", "last_health_check_at"}:
                if key in kwargs:
                    config[key] = kwargs.pop(key)
            kwargs["config_json"] = config
            if "last_health_check_at" in kwargs:
                kwargs["last_tested_at"] = kwargs.pop("last_health_check_at")
        super().__init__(**kwargs)

    @property
    def type(self) -> str:
        return self.provider.lower()

    @property
    def environment(self) -> str:
        return str(self.config_json.get("environment", "DEV"))

    @property
    def host(self) -> str:
        return str(self.config_json.get("host", ""))

    @property
    def port(self) -> int:
        return int(self.config_json.get("port", 0))

    @property
    def database_name(self) -> str:
        return str(self.config_json.get("database_name", ""))

    @property
    def username(self) -> str:
        return str(self.config_json.get("username", ""))

    @property
    def ssl_enabled(self) -> bool:
        return bool(self.config_json.get("ssl_enabled", False))

    @property
    def provider_options(self) -> dict:
        return dict(self.config_json.get("provider_options", {}))

    @property
    def last_health_check_at(self) -> datetime | None:
        return self.last_tested_at


class PipelineDestination(Entity, Base):
    __tablename__ = "pipeline_destinations"
    __table_args__ = (UniqueConstraint("destination_connection_id", "name"),)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id"), index=True)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        "destination_connection_id", ForeignKey("connections.id"), index=True
    )
    connector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("connectors.id"), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    delivery_type: Mapped[str] = mapped_column(String(30), default="DATABASE")
    delivery_mode: Mapped[str] = mapped_column(String, default="upsert")
    topic_mapping_json: Mapped[list] = mapped_column(JSON, default=list)
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict)
    desired_state: Mapped[str] = mapped_column(String, default="RUNNING")
    actual_state: Mapped[str] = mapped_column(String, default="UNKNOWN", index=True)


class SchemaVersion(Entity, Base):
    __tablename__ = "schema_versions"
    __table_args__ = (
        UniqueConstraint("source_connection_id", "schema_name", "table_name", "version"),
        Index("ix_schema_lookup", "source_connection_id", "schema_name", "table_name"),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        "source_connection_id", ForeignKey("connections.id", ondelete="CASCADE")
    )
    schema_name: Mapped[str] = mapped_column(String)
    table_name: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer)
    schema_json: Mapped[dict] = mapped_column(JSON)
    schema_hash: Mapped[str] = mapped_column(String(64))
    diff_json: Mapped[list] = mapped_column(JSON, default=list)


class PipelineEvent(Entity, Base):
    """Operational events only, never CDC row payloads."""

    __tablename__ = "pipeline_events"
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipelines.id", ondelete="SET NULL"), index=True
    )
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        "destination_connection_id",
        ForeignKey("connections.id", ondelete="SET NULL"),
        index=True,
    )
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_destinations.id", ondelete="SET NULL"), index=True
    )
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connectors.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[str] = mapped_column(String)
    component: Mapped[str] = mapped_column(String(80), default="pipeline")
    error_code: Mapped[str | None] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    technical_details: Mapped[dict] = mapped_column(JSON, default=dict)
    recoverable: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="OPEN")
    connector_name: Mapped[str | None] = mapped_column(String)
    task_id: Mapped[int | None] = mapped_column(Integer)


class PipelineMetric(Entity, Base):
    __tablename__ = "pipeline_metrics"
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id", ondelete="CASCADE"))
    values_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class AlertRule(Entity, Base):
    __tablename__ = "alert_rules"
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(1000), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    severity: Mapped[str | None] = mapped_column(String(20))
    event_types: Mapped[list] = mapped_column(JSON, default=list)
    source_filters: Mapped[list] = mapped_column(JSON, default=list)
    pipeline_filters: Mapped[list] = mapped_column(JSON, default=list)
    connector_filters: Mapped[list] = mapped_column(JSON, default=list)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=900)
    notification_policy: Mapped[str] = mapped_column(String(40), default="notify_after_cooldown")
    send_recovery: Mapped[bool] = mapped_column(Boolean, default=True)
    # Kept for compatibility with installations that used the original generic rule model.
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)


class NotificationChannel(Entity, Base):
    __tablename__ = "notification_channels"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    type: Mapped[str] = mapped_column(String(20), index=True)
    config_encrypted: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("secret_references.id", ondelete="RESTRICT")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class AlertRuleChannel(Base):
    __tablename__ = "alert_rule_channels"
    alert_rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), primary_key=True
    )


class Alert(Entity, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_fingerprint_status", "fingerprint", "status"),
        Index("ix_alerts_pipeline_severity", "pipeline_id", "severity"),
        Index("ix_alerts_created_at", "created_at"),
        Index(
            "uq_alerts_active_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text("status IN ('firing', 'acknowledged', 'silenced')"),
            sqlite_where=text("status IN ('firing', 'acknowledged', 'silenced')"),
        ),
    )
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default="firing", index=True)
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(120), index=True)
    source_name: Mapped[str | None] = mapped_column(String(255))
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipelines.id", ondelete="SET NULL"), index=True
    )
    pipeline_name: Mapped[str | None] = mapped_column(String(120))
    component: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(String(2000))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    silenced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    silenced_by: Mapped[str | None] = mapped_column(String(255))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)


class NotificationDelivery(Entity, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("alert_id", "channel_id", "kind"),
        Index("ix_notification_delivery_queue", "status", "next_attempt_at"),
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="firing")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    claimed_by: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Entity, Base):
    __tablename__ = "audit_logs"
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String, index=True)
    resource_type: Mapped[str] = mapped_column(String)
    resource_id: Mapped[str] = mapped_column(String)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (Index("ix_audit_created", "created_at"),)


class KafkaTopic(Entity, Base):
    __tablename__ = "kafka_topics"
    __table_args__ = (UniqueConstraint("kafka_cluster_id", "name"),)
    kafka_cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kafka_clusters.id"))
    name: Mapped[str] = mapped_column(String)
    metadata_json: Mapped[dict] = mapped_column(JSON)


class Job(Entity, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "next_attempt_at", "lease_expires_at"),)
    kind: Mapped[str] = mapped_column(String)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    actor: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="PENDING", index=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String)
    claimed_by: Mapped[str | None] = mapped_column(String(120), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# Import compatibility only. Both names resolve to the same mapped class/table.
Source = Connection
Destination = Connection
