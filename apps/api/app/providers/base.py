from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.models.entities import Destination, Source
from app.schemas.requests import PipelineInput


class Capability(StrEnum):
    CDC_SOURCE = "CDC_SOURCE"
    CDC_DESTINATION = "CDC_DESTINATION"
    SNAPSHOT = "SNAPSHOT"
    INCREMENTAL_SNAPSHOT = "INCREMENTAL_SNAPSHOT"
    SCHEMA_DISCOVERY = "SCHEMA_DISCOVERY"
    SCHEMA_EVOLUTION = "SCHEMA_EVOLUTION"
    DELETE_PROPAGATION = "DELETE_PROPAGATION"
    SSL = "SSL"


@dataclass(frozen=True)
class ProviderMetadata:
    type: str
    display_name: str
    icon: str
    default_port: int
    namespace_label: str
    source_supported: bool
    destination_supported: bool
    capabilities: tuple[Capability, ...]
    setup_instructions: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "display_name": self.display_name,
            "icon": self.icon,
            "default_port": self.default_port,
            "namespace_label": self.namespace_label,
            "source_supported": self.source_supported,
            "destination_supported": self.destination_supported,
            "capabilities": list(self.capabilities),
            "setup_instructions": list(self.setup_instructions),
        }


class DatabaseProvider(Protocol):
    metadata: ProviderMetadata

    def source_adapter(self, source: Source, password: str): ...
    def destination_adapter(self, destination: Destination, password: str): ...
    def build_source_config(
        self,
        source: Source,
        pipeline: PipelineInput,
        password: str,
        connector_name: str,
        kafka_bootstrap_servers: str,
        enable_signals: bool = False,
    ) -> dict[str, str]: ...
    def build_destination_url(self, destination: Destination) -> str: ...
    def immutable_connector_keys(self) -> tuple[str, ...]: ...
    def signal_service(self, database, source: Source, topic_prefix: str): ...
    def publication_manager(self, database, connector_config: dict): ...
