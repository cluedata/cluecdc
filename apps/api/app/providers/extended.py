"""New sources use the existing provider and connector lifecycle contracts."""

import hashlib
import re

from app.core.errors import DomainError
from app.providers.base import Capability, ProviderMetadata
from app.providers.source_capabilities import (
    CONNECTOR_CLASSES,
    require_table_management,
    validate_capture,
)
from app.services.debezium import message_key_columns, notification_topic


class SourceOnlyProvider:
    def destination_adapter(self, destination, password):
        raise DomainError("SOURCE_ONLY_PROVIDER", "This provider supports capture only", 422)

    def build_destination_url(self, destination):
        raise DomainError("SOURCE_ONLY_PROVIDER", "This provider supports capture only", 422)

    def immutable_connector_keys(self):
        return (
            "topic.prefix",
            "database.names",
            "database.dbname",
            "database.pdb.name",
            "schema.history.internal.kafka.topic",
            "mongodb.connection.string",
        )

    def signal_service(self, database, source, topic_prefix):
        require_table_management(source.type)

    def publication_manager(self, database, connector_config):
        from app.services.publication import NoopPublicationManager

        return NoopPublicationManager()

    def build_source_config(
        self,
        source,
        pipeline,
        password,
        connector_name,
        kafka_bootstrap_servers,
        enable_signals=False,
    ):
        validate_capture(source.type, pipeline)
        if enable_signals:
            require_table_management(source.type)
        suffix = hashlib.sha256(pipeline.topic_prefix.encode()).hexdigest()[:20]
        config = {
            "name": connector_name,
            "connector.class": CONNECTOR_CLASSES[source.type],
            "tasks.max": "1",
            "topic.prefix": pipeline.topic_prefix,
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
            "event.processing.failure.handling.mode": "fail",
        }
        collections = ",".join(
            re.escape(f"{t.schema_name}.{t.table_name}") for t in pipeline.tables
        )
        timeout = int(source.provider_options.get("connection_timeout_seconds", 10))
        if source.type == "mongodb":
            config.update(
                {
                    "mongodb.connection.string": password,
                    "mongodb.ssl.enabled": str(source.ssl_enabled).lower(),
                    "mongodb.ssl.invalid.hostname.allowed": "false",
                    "collection.include.list": collections,
                    "database.include.list": re.escape(source.database_name),
                    "capture.mode": "change_streams",
                    "connect.timeout.ms": str(timeout * 1000),
                }
            )
            return config
        config.update(
            {
                "database.hostname": source.host,
                "database.port": str(source.port),
                "database.user": source.username,
                "database.password": password,
                "table.include.list": collections,
                "schema.history.internal.kafka.bootstrap.servers": kafka_bootstrap_servers,
                "schema.history.internal.kafka.topic": f"cluecdc.schema-history.{suffix}",
                "include.schema.changes": "false",
                "decimal.handling.mode": "precise",
                "binary.handling.mode": "bytes",
            }
        )
        if source.type == "sqlserver":
            config.update(
                {
                    "database.names": source.database_name,
                    "database.encrypt": str(source.ssl_enabled).lower(),
                    "database.trustServerCertificate": "false",
                    "database.loginTimeout": str(timeout),
                }
            )
        elif source.type == "oracle":
            options = source.provider_options
            # database_name is the CDB name; PDB/service identity is separate.
            config.update(
                {
                    "database.dbname": source.database_name,
                    "database.connection.adapter": "logminer",
                    "database.query.timeout.ms": str(timeout * 1000),
                }
            )
            if options.get("pdb_name"):
                config["database.pdb.name"] = str(options["pdb_name"])
            service = options.get("service_name") or options.get("sid") or source.database_name
            protocol = "TCPS" if source.ssl_enabled else "TCP"
            identity = "SID" if options.get("sid") else "SERVICE_NAME"
            # Values are validated identifiers; arbitrary JDBC descriptors are not accepted.
            config["database.url"] = (
                f"jdbc:oracle:thin:@(DESCRIPTION=(ADDRESS=(PROTOCOL={protocol})"
                f"(HOST={source.host})(PORT={source.port}))"
                f"(CONNECT_DATA=({identity}={service}))"
                "(SECURITY=(SSL_SERVER_DN_MATCH=YES)))"
            )
        if keys := message_key_columns(pipeline.tables):
            config["message.key.columns"] = keys
        return config


class SQLServerProvider(SourceOnlyProvider):
    metadata = ProviderMetadata(
        type="sqlserver",
        display_name="SQL Server",
        icon="database",
        default_port=1433,
        namespace_label="Schema",
        source_supported=True,
        destination_supported=False,
        capabilities=(
            Capability.CDC_SOURCE,
            Capability.SNAPSHOT,
            Capability.SCHEMA_DISCOVERY,
            Capability.DELETE_PROPAGATION,
            Capability.SSL,
        ),
        setup_instructions=(
            "DBA: enable database and table CDC; start SQL Server Agent.",
            "Grant SELECT and database state/capture-instance access.",
            "Configure cleanup retention to exceed maximum outages.",
        ),
    )

    def source_adapter(self, source, password):
        from app.adapters.sqlserver import SQLServerAdapter

        return SQLServerAdapter(source, password)


class MongoDBProvider(SourceOnlyProvider):
    metadata = ProviderMetadata(
        type="mongodb",
        display_name="MongoDB",
        icon="database",
        default_port=27017,
        namespace_label="Database / collection",
        source_supported=True,
        destination_supported=False,
        capabilities=(
            Capability.CDC_SOURCE,
            Capability.SNAPSHOT,
            Capability.SCHEMA_DISCOVERY,
            Capability.DELETE_PROPAGATION,
            Capability.SSL,
        ),
        setup_instructions=(
            "Use a replica set and grant find, changeStream and listCollections.",
            "Store the complete URI in encrypted credentials, including Atlas SRV URIs.",
            "Only raw event-log delivery is enabled; patches are not full-row upserts.",
        ),
    )

    def source_adapter(self, source, password):
        from app.adapters.mongodb import MongoDBAdapter

        return MongoDBAdapter(source, password)


class OracleProvider(SourceOnlyProvider):
    metadata = ProviderMetadata(
        type="oracle",
        display_name="Oracle",
        icon="database",
        default_port=1521,
        namespace_label="Schema",
        source_supported=True,
        destination_supported=False,
        capabilities=(
            Capability.CDC_SOURCE,
            Capability.SNAPSHOT,
            Capability.SCHEMA_DISCOVERY,
            Capability.DELETE_PROPAGATION,
            Capability.SSL,
        ),
        setup_instructions=(
            "Use LogMiner with ARCHIVELOG and supplemental logging enabled by a DBA.",
            "Grant documented LogMiner and catalog privileges; retain archives.",
            (
                "Install a licensed Oracle JDBC driver on every Connect worker; "
                "live Oracle E2E is unverified."
            ),
        ),
    )

    def source_adapter(self, source, password):
        from app.adapters.oracle import OracleAdapter

        return OracleAdapter(source, password)
