from app.adapters.mariadb import MariaDBAdapter
from app.providers.base import Capability, ProviderMetadata
from app.providers.mysql import MySQLProvider
from app.providers.source_capabilities import validate_capture
from app.services.debezium import MySQLDebeziumConfigBuilder


class MariaDBProvider(MySQLProvider):
    metadata = ProviderMetadata(
        type="mariadb",
        display_name="MariaDB",
        icon="database-zap",
        default_port=3306,
        namespace_label="Database",
        source_supported=True,
        destination_supported=False,
        capabilities=tuple(
            c for c in MySQLProvider.metadata.capabilities if c != Capability.CDC_DESTINATION
        ),
        setup_instructions=(
            "Enable ROW/FULL binary logs and a unique server_id.",
            "Grant SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, "
            "BINLOG MONITOR and LOCK TABLES.",
            "Retain binlogs longer than the maximum outage window.",
        ),
    )

    def source_adapter(self, source, password):
        return MariaDBAdapter(source, password)

    def build_source_config(
        self,
        source,
        pipeline,
        password,
        connector_name,
        kafka_bootstrap_servers,
        enable_signals=False,
    ):
        validate_capture("mariadb", pipeline)
        config = MySQLDebeziumConfigBuilder().build(
            source,
            pipeline,
            password,
            enable_signals,
            connector_name,
            kafka_bootstrap_servers,
        )
        config["connector.class"] = "io.debezium.connector.mariadb.MariaDbConnector"
        # 3.3.2.Final runtime validation uses MariaDB JDBC enum values, which
        # differ from the published MySQL-like table in the 3.3 documentation.
        config["database.ssl.mode"] = "verify-full" if source.ssl_enabled else "disable"
        config["event.converting.failure.handling.mode"] = "fail"
        config["connect.timeout.ms"] = str(
            int(source.provider_options.get("connection_timeout_seconds", 10)) * 1000
        )
        return config

    def destination_adapter(self, destination, password):
        from app.core.errors import DomainError

        raise DomainError("SOURCE_ONLY_PROVIDER", "MariaDB is a source-only provider", 422)

    def build_destination_url(self, destination):
        from app.core.errors import DomainError

        raise DomainError("SOURCE_ONLY_PROVIDER", "MariaDB is a source-only provider", 422)
