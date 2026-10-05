from dataclasses import dataclass
from typing import Protocol

from app.core.errors import DomainError
from app.models.entities import Connection
from app.schemas.requests import DeliveryInput
from app.services.destinations.config import JDBC_CLASS, DestinationConfigBuilder

S3_CLASS = "io.aiven.kafka.connect.s3.AivenKafkaConnectS3SinkConnector"


class DeliveryProvider(Protocol):
    @property
    def connector_class(self) -> str: ...

    @property
    def delivery_type(self) -> str: ...

    @property
    def needs_relational_metadata(self) -> bool: ...

    def build_config(
        self,
        connection: Connection,
        data: DeliveryInput,
        metadata: dict,
        connector_name: str,
        *,
        has_session_token: bool = False,
    ) -> dict[str, str]: ...


@dataclass(frozen=True)
class JdbcDeliveryProvider:
    connector_class: str = JDBC_CLASS
    delivery_type: str = "DATABASE"
    needs_relational_metadata: bool = True

    def build_config(
        self,
        connection: Connection,
        data: DeliveryInput,
        metadata: dict,
        connector_name: str,
        *,
        has_session_token: bool = False,
    ) -> dict[str, str]:
        return DestinationConfigBuilder().build(connection, data, metadata, connector_name)


@dataclass(frozen=True)
class ObjectStorageDeliveryProvider:
    connector_class: str = S3_CLASS
    delivery_type: str = "OBJECT_STORAGE"
    needs_relational_metadata: bool = False

    def build_config(
        self,
        connection: Connection,
        data: DeliveryInput,
        metadata: dict,
        connector_name: str,
        *,
        has_session_token: bool = False,
    ) -> dict[str, str]:
        if data.delivery_type != "OBJECT_STORAGE":
            raise DomainError(
                "DELIVERY_TYPE_MISMATCH",
                "Object storage requires an object-storage delivery configuration",
                422,
            )
        config = connection.config_json
        prefix = str(config.get("prefix", "")).strip("/")
        template = data.file_name_template
        if data.compression == "none" and template.endswith(".gz"):
            template = template[:-3]
        if prefix:
            template = f"{prefix}/{template}"
        secret_ref = connection.secret_ref
        if secret_ref is None:
            raise DomainError("CONNECTION_CREDENTIALS_REQUIRED", "Credentials are required", 422)
        result = {
            "name": connector_name,
            "connector.class": self.connector_class,
            "tasks.max": str(data.tasks_max),
            "topics": ",".join(data.topics),
            "key.converter": "org.apache.kafka.connect.json.JsonConverter",
            "key.converter.schemas.enable": "false",
            "value.converter": "org.apache.kafka.connect.json.JsonConverter",
            "value.converter.schemas.enable": "false",
            "aws.s3.bucket.name": str(config["bucket"]),
            "aws.s3.region": str(config.get("region", "us-east-1")),
            "format.output.type": "jsonl",
            "format.output.fields": "key,value,offset,timestamp",
            "format.output.envelope": "true",
            "file.compression.type": data.compression,
            "file.max.records": str(data.file_max_records),
            "file.name.template": template,
            "offset.flush.interval.ms": str(data.flush_interval_ms),
            "errors.tolerance": "none" if data.error_policy == "fail" else "all",
            "errors.log.enable": "false",
            "consumer.override.auto.offset.reset": "earliest",
            "consumer.override.group.id": connector_name,
        }
        if config.get("endpoint"):
            result["aws.s3.endpoint"] = str(config["endpoint"])
        if has_session_token:
            result.update(
                {
                    "aws.credentials.provider": "io.cluecdc.connect.ClueSessionCredentialsProvider",
                    "cluecdc.access.key": f"${{cluecdc:{secret_ref}:access_key}}",
                    "cluecdc.secret.key": f"${{cluecdc:{secret_ref}:secret_key}}",
                    "cluecdc.session.token": f"${{cluecdc:{secret_ref}:session_token}}",
                }
            )
        else:
            result["aws.access.key.id"] = f"${{cluecdc:{secret_ref}:access_key}}"
            result["aws.secret.access.key"] = f"${{cluecdc:{secret_ref}:secret_key}}"
        return result


_JDBC = JdbcDeliveryProvider()
_OBJECT_STORAGE = ObjectStorageDeliveryProvider()


def delivery_provider(connection: Connection) -> DeliveryProvider:
    if connection.category == "DATABASE" and connection.provider in {"POSTGRESQL", "MYSQL"}:
        return _JDBC
    if connection.category == "OBJECT_STORAGE" and connection.provider in {"AWS_S3", "MINIO"}:
        return _OBJECT_STORAGE
    raise DomainError("DELIVERY_PROVIDER_UNSUPPORTED", "Delivery provider is not supported", 422)
