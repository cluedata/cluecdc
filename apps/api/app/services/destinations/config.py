import json

from app.models.entities import Destination
from app.providers import get_provider
from app.schemas.requests import DeliveryInput

JDBC_CLASS = "io.debezium.connector.jdbc.JdbcSinkConnector"


class DestinationConfigBuilder:
    def build(
        self, destination: Destination, data: DeliveryInput, metadata: dict, name: str
    ) -> dict:
        config = {
            "name": name,
            "connector.class": JDBC_CLASS,
            "tasks.max": str(data.tasks_max),
            "topics": ",".join(mapping.topic for mapping in data.mappings),
            "connection.url": get_provider(destination.type).build_destination_url(destination),
            "connection.username": destination.username,
            "connection.password": f"${{cluecdc:{destination.secret_ref}:password}}",
            "connection.pool.min_size": "1",
            "connection.pool.max_size": "4",
            "insert.mode": data.write_mode,
            "primary.key.mode": data.primary_key_mode,
            "delete.enabled": str(data.delete_enabled).lower(),
            "schema.evolution": "basic" if data.auto_evolve else "none",
            "collection.name.format": "${source.schema}.${source.table}",
            "quote.identifiers": "true",
            "batch.size": str(data.batch_size),
            "flush.max.retries": str(data.max_retries),
            "flush.retry.delay.ms": str(data.retry_backoff_ms),
            "use.time.zone": "UTC",
            "key.converter": "org.apache.kafka.connect.json.JsonConverter",
            "key.converter.schemas.enable": "false",
            "value.converter": "org.apache.kafka.connect.json.JsonConverter",
            "value.converter.schemas.enable": "false",
            "transforms": "delivery",
            "transforms.delivery.type": "io.cluecdc.connect.ClueDeliveryTransform",
            "transforms.delivery.metadata": json.dumps(metadata, separators=(",", ":")),
            "errors.tolerance": "none",
            "errors.log.enable": "false",
            "consumer.override.auto.offset.reset": "earliest",
            "consumer.override.group.id": name,
        }
        return config
