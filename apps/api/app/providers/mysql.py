from urllib.parse import quote

from app.adapters.mysql import MySQLAdapter
from app.models.entities import Destination, Source
from app.providers.base import Capability, ProviderMetadata
from app.services.debezium import MySQLDebeziumConfigBuilder


class MySQLProvider:
    metadata = ProviderMetadata(
        type="mysql",
        display_name="MySQL",
        icon="database-zap",
        default_port=3306,
        namespace_label="Database",
        source_supported=True,
        destination_supported=True,
        capabilities=tuple(Capability),
        setup_instructions=(
            "Enable binary logging with binlog_format=ROW and binlog_row_image=FULL.",
            "Grant SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE and REPLICATION CLIENT.",
            "Use a unique server.id and retain binlogs longer than the maximum outage window.",
        ),
    )

    def source_adapter(self, source: Source, password: str):
        return MySQLAdapter(source, password)

    def destination_adapter(self, destination: Destination, password: str):
        from app.services.destinations.adapter import MySQLDestinationAdapter

        return MySQLDestinationAdapter(destination, password)

    def build_source_config(
        self,
        source,
        pipeline,
        password,
        connector_name,
        kafka_bootstrap_servers,
        enable_signals=False,
    ):
        return MySQLDebeziumConfigBuilder().build(
            source,
            pipeline,
            password,
            enable_signals,
            connector_name=connector_name,
            kafka_bootstrap_servers=kafka_bootstrap_servers,
        )

    def build_destination_url(self, destination: Destination) -> str:
        database = quote(destination.database_name, safe="")
        ssl = "VERIFY_IDENTITY" if destination.ssl_enabled else "DISABLED"
        return (
            f"jdbc:mysql://{destination.host}:{destination.port}/{database}"
            f"?sslMode={ssl}&useUnicode=true&characterEncoding=UTF-8&serverTimezone=UTC"
        )

    def immutable_connector_keys(self) -> tuple[str, ...]:
        return ("database.server.id", "topic.prefix", "schema.history.internal.kafka.topic")

    def signal_service(self, database, source: Source, topic_prefix: str):
        from app.services.snapshots import MySQLDebeziumSignalService

        return MySQLDebeziumSignalService(database, topic_prefix, source.database_name)

    def publication_manager(self, database, connector_config: dict):
        from app.services.publication import NoopPublicationManager

        return NoopPublicationManager()
