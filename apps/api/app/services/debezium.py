import hashlib
import re
from typing import Protocol

from app.models.entities import Source
from app.schemas.requests import PipelineInput


class DebeziumConfigBuilder(Protocol):
    def build(
        self,
        source: Source,
        pipeline: PipelineInput,
        password: str,
        enable_signals: bool = False,
        connector_name: str | None = None,
        kafka_bootstrap_servers: str = "kafka:29092",
    ) -> dict[str, str]: ...


class PostgresDebeziumConfigBuilder:
    def build(
        self,
        source: Source,
        pipeline: PipelineInput,
        password: str,
        enable_signals: bool = False,
        connector_name: str | None = None,
        kafka_bootstrap_servers: str = "kafka:29092",
    ) -> dict[str, str]:
        # Stable, isolated slot/publication names per pipeline; valid PG identifiers.
        suffix = hashlib.sha256(pipeline.topic_prefix.encode()).hexdigest()[:20]
        signal_table = f"public.cluecdc_signal_{suffix}"
        config = {
            "name": connector_name or f"cluecdc-{pipeline.topic_prefix}",
            "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
            "tasks.max": "1",
            "database.hostname": source.host,
            "database.port": str(source.port),
            "database.user": source.username,
            "database.password": password,
            "database.dbname": source.database_name,
            "database.sslmode": "verify-full" if source.ssl_enabled else "disable",
            "topic.prefix": pipeline.topic_prefix,
            "plugin.name": "pgoutput",
            "slot.name": f"cluecdc_{suffix}",
            "publication.name": f"cluecdc_{suffix}",
            "publication.autocreate.mode": "filtered",
            "table.include.list": ",".join(
                re.escape(f"{t.schema_name}.{t.table_name}") for t in pipeline.tables
            ),
            "snapshot.mode": pipeline.snapshot_mode,
            "heartbeat.interval.ms": str(pipeline.heartbeat_interval_ms),
            "max.batch.size": str(pipeline.max_batch_size),
            "max.queue.size": str(pipeline.max_queue_size),
            "poll.interval.ms": str(pipeline.poll_interval_ms),
            "key.converter": "org.apache.kafka.connect.json.JsonConverter",
            "key.converter.schemas.enable": "false",
            "value.converter": "org.apache.kafka.connect.json.JsonConverter",
            "value.converter.schemas.enable": "false",
            "tombstones.on.delete": "true",
            "notification.enabled.channels": "sink",
            "notification.sink.topic.name": notification_topic(pipeline.topic_prefix),
            # Slots survive connector deletion; explicit DBA cleanup is documented.
            "slot.drop.on.stop": "false",
        }
        if enable_signals:
            config.update(
                {
                    "table.include.list": config["table.include.list"]
                    + ","
                    + re.escape(signal_table),
                    "signal.data.collection": signal_table,
                    "signal.enabled.channels": "source",
                }
            )
        config.update(pipeline.additional_debezium_properties)
        return config


class MySQLDebeziumConfigBuilder:
    SNAPSHOT_MODES = {
        "initial": "initial",
        "no_data": "no_data",
        "never": "never",
        "always": "always",
    }

    def build(
        self,
        source: Source,
        pipeline: PipelineInput,
        password: str,
        enable_signals: bool = False,
        connector_name: str | None = None,
        kafka_bootstrap_servers: str = "kafka:29092",
    ) -> dict[str, str]:
        suffix = hashlib.sha256(pipeline.topic_prefix.encode()).hexdigest()[:20]
        databases = sorted({table.schema_name for table in pipeline.tables})
        signal_table = f"{source.database_name}.{signal_table_name(pipeline.topic_prefix)}"
        server_id = pipeline.provider_options.get("server_id") or source.provider_options.get(
            "server_id"
        )
        if not server_id:
            raise ValueError("A persisted MySQL server_id is required")
        config = {
            "name": connector_name or f"cluecdc-{pipeline.topic_prefix}",
            "connector.class": "io.debezium.connector.mysql.MySqlConnector",
            "tasks.max": "1",
            "database.hostname": source.host,
            "database.port": str(source.port),
            "database.user": source.username,
            "database.password": password,
            "database.server.id": str(server_id),
            "database.ssl.mode": "verify_identity" if source.ssl_enabled else "disabled",
            "topic.prefix": pipeline.topic_prefix,
            "database.include.list": ",".join(re.escape(value) for value in databases),
            "table.include.list": ",".join(
                re.escape(f"{t.schema_name}.{t.table_name}") for t in pipeline.tables
            ),
            "snapshot.mode": self.SNAPSHOT_MODES[pipeline.snapshot_mode],
            "schema.history.internal.kafka.bootstrap.servers": kafka_bootstrap_servers,
            "schema.history.internal.kafka.topic": f"cluecdc.schema-history.{suffix}",
            "include.schema.changes": "false",
            "heartbeat.interval.ms": str(pipeline.heartbeat_interval_ms),
            "max.batch.size": str(pipeline.max_batch_size),
            "max.queue.size": str(pipeline.max_queue_size),
            "poll.interval.ms": str(pipeline.poll_interval_ms),
            "key.converter": "org.apache.kafka.connect.json.JsonConverter",
            "key.converter.schemas.enable": "false",
            "value.converter": "org.apache.kafka.connect.json.JsonConverter",
            "value.converter.schemas.enable": "false",
            "tombstones.on.delete": "true",
            "notification.enabled.channels": "sink",
            "notification.sink.topic.name": notification_topic(pipeline.topic_prefix),
            "decimal.handling.mode": "precise",
            "binary.handling.mode": "bytes",
        }
        if enable_signals:
            if source.database_name not in databases:
                config["database.include.list"] += "," + re.escape(source.database_name)
            config.update(
                {
                    "table.include.list": config["table.include.list"]
                    + ","
                    + re.escape(signal_table),
                    "signal.data.collection": signal_table,
                    "signal.enabled.channels": "source",
                }
            )
        config.update(pipeline.additional_debezium_properties)
        return config


def signal_table_name(topic_prefix: str) -> str:
    suffix = hashlib.sha256(topic_prefix.encode()).hexdigest()[:20]
    return f"cluecdc_signal_{suffix}"


def notification_topic(topic_prefix: str) -> str:
    return f"cluecdc.notifications.{topic_prefix}"


def derive_actual_state(status: dict) -> str:
    state = status.get("connector", {}).get("state", "UNKNOWN")
    tasks = status.get("tasks", [])
    states = [t.get("state", "UNKNOWN") for t in tasks]
    if state == "FAILED":
        return "FAILED"
    if "FAILED" in states:
        return "DEGRADED" if state == "RUNNING" else "FAILED"
    if state == "PAUSED":
        return "PAUSED"
    if state == "RUNNING" and states and all(s == "RUNNING" for s in states):
        return "RUNNING"
    return "UNKNOWN"
