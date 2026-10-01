from urllib.parse import quote

from app.adapters.postgres import PostgresAdapter
from app.models.entities import Destination, Source
from app.providers.base import Capability, ProviderMetadata
from app.services.debezium import PostgresDebeziumConfigBuilder


class PostgreSQLProvider:
    metadata = ProviderMetadata(
        type="postgresql",
        display_name="PostgreSQL",
        icon="database",
        default_port=5432,
        namespace_label="Schema",
        source_supported=True,
        destination_supported=True,
        capabilities=tuple(Capability),
        setup_instructions=(
            "Set wal_level=logical and allocate replication slots and WAL senders.",
            "Grant REPLICATION and SELECT; allow ClueCDC to manage the filtered publication.",
        ),
    )

    def source_adapter(self, source: Source, password: str):
        return PostgresAdapter(source, password)

    def destination_adapter(self, destination: Destination, password: str):
        from app.services.destinations.adapter import PostgresDestinationAdapter

        return PostgresDestinationAdapter(destination, password)

    def build_source_config(
        self,
        source,
        pipeline,
        password,
        connector_name,
        kafka_bootstrap_servers,
        enable_signals=False,
    ):
        return PostgresDebeziumConfigBuilder().build(
            source,
            pipeline,
            password,
            enable_signals,
            connector_name=connector_name,
            kafka_bootstrap_servers=kafka_bootstrap_servers,
        )

    def build_destination_url(self, destination: Destination) -> str:
        host = f"[{destination.host}]" if ":" in destination.host else destination.host
        database = quote(destination.database_name, safe="")
        sslmode = "verify-full" if destination.ssl_enabled else "disable"
        return f"jdbc:postgresql://{host}:{destination.port}/{database}?sslmode={sslmode}"

    def immutable_connector_keys(self) -> tuple[str, ...]:
        return ("slot.name", "publication.name", "topic.prefix")

    def signal_service(self, database, source: Source, topic_prefix: str):
        from app.services.snapshots import DebeziumSignalService

        return DebeziumSignalService(database, topic_prefix)

    def publication_manager(self, database, connector_config: dict):
        from app.services.publication import PostgresPublicationManager

        return PostgresPublicationManager(database, connector_config["publication.name"])
