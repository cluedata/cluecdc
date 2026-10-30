"""Explicit source contracts; verification is evidence, never inferred from a name."""

from copy import deepcopy

from app.core.errors import DomainError

NEW_SOURCES = {"mariadb", "sqlserver", "mongodb", "oracle"}
CONNECTOR_CLASSES = {
    "postgresql": "io.debezium.connector.postgresql.PostgresConnector",
    "mysql": "io.debezium.connector.mysql.MySqlConnector",
    "mariadb": "io.debezium.connector.mariadb.MariaDbConnector",
    "sqlserver": "io.debezium.connector.sqlserver.SqlServerConnector",
    "mongodb": "io.debezium.connector.mongodb.MongoDbConnector",
    "oracle": "io.debezium.connector.oracle.OracleConnector",
}

_CONTRACTS = {
    "postgresql": {"versions": "Existing PostgreSQL contract", "incremental_snapshot": True},
    "mysql": {"versions": "MySQL 8.x", "incremental_snapshot": True},
    "mariadb": {
        "versions": "MariaDB 10.6, 10.11, 11.4 (3.3 connector compatibility)",
        "incremental_snapshot": True,
        "limitations": ["ROW/FULL binlogs required; spatial mappings need event-log delivery"],
    },
    "sqlserver": {
        "versions": "SQL Server 2016 SP1+ Standard/Enterprise/Developer; validate CDC edition",
        "incremental_snapshot": False,
        "limitations": ["CDC and capture instances must be enabled by an administrator"],
    },
    "mongodb": {
        "versions": "MongoDB 6.0, 7.0, 8.0; replica set (sharding unverified)",
        "incremental_snapshot": False,
        "limitations": [
            "Raw extended-JSON envelopes only; no relational flattening or stateful upserts",
            "Partial updates remain patches; no state reconstruction is promised",
        ],
    },
    "oracle": {
        "versions": "Oracle 19c/21c, LogMiner; live E2E not verified",
        "incremental_snapshot": False,
        "limitations": [
            "Operator-provided Oracle JDBC driver required on Connect workers",
            "LOB/XML/JSON and unconstrained NUMBER stateful delivery unverified",
        ],
    },
}


def source_capabilities(source_type: str) -> dict:
    contract = deepcopy(_CONTRACTS[source_type])
    new = source_type in NEW_SOURCES
    document = source_type == "mongodb"
    return {
        **contract,
        "status": "EXPERIMENTAL" if new else "SUPPORTED",
        "verification": "NOT_RUN" if new else "EXISTING_REGRESSION_SUITE",
        "connector_class": CONNECTOR_CLASSES[source_type],
        "debezium_version": "3.3.2.Final",
        "authentication": ["USERNAME_PASSWORD", "ENCRYPTED_URI"] if document else ["PASSWORD"],
        "collection_kind": "DOCUMENT" if document else "RELATIONAL",
        "discovery": ["DATABASE", "COLLECTION", "DOCUMENT_ID"]
        if document else ["SCHEMA", "TABLE", "COLUMN", "PRIMARY_KEY", "UNIQUE_INDEX"],
        "snapshot_modes": ["initial", "no_data"] if new else ["initial", "no_data", "never", "always"],
        "row_identity": ["_id"] if document else ["PRIMARY_KEY", "NON_NULL_UNIQUE", "VALIDATED_CUSTOM"],
        "crud": "SUPPORTED_UNVERIFIED" if new else "SUPPORTED",
        "schema_evolution": "CONDITIONAL",
        "recovery": "DURABLE_DEBEZIUM_OFFSETS_OPERATOR_ACTION_IF_HISTORY_EXPIRES",
        "table_management": "CONDITIONAL" if contract["incremental_snapshot"] else "UNSUPPORTED",
        "destination_modes": {
            "AWS_S3": ["EVENT_LOG"], "MINIO": ["EVENT_LOG"],
            **({"POSTGRESQL": ["CURRENT_STATE"], "MYSQL": ["CURRENT_STATE"]}
               if source_type in {"postgresql", "mysql", "mariadb"} else {}),
        },
    }


def validate_capture(source_type: str, pipeline) -> None:
    if source_type not in NEW_SOURCES:
        return
    modes = source_capabilities(source_type)["snapshot_modes"]
    if pipeline.snapshot_mode not in modes:
        raise DomainError("SOURCE_SNAPSHOT_MODE_UNSUPPORTED", "Select initial or no_data", 422)
    # Additional properties are not an escape hatch for credentials, offset resets,
    # event format changes, mining adapters or silently dropping unsupported values.
    if pipeline.additional_debezium_properties:
        raise DomainError(
            "SOURCE_PROPERTIES_UNVERIFIED",
            "Additional Debezium properties are not enabled for experimental sources", 422,
        )


def validate_delivery(source_type: str, destination_provider: str, write_mode: str) -> None:
    if source_type not in NEW_SOURCES:
        return
    mode = "EVENT_LOG" if destination_provider in {"AWS_S3", "MINIO"} else "CURRENT_STATE"
    allowed = source_capabilities(source_type)["destination_modes"].get(destination_provider, [])
    if mode not in allowed or (mode == "CURRENT_STATE" and write_mode != "upsert"):
        raise DomainError(
            "SOURCE_DESTINATION_UNSUPPORTED",
            "This source/delivery combination is unverified or cannot safely consume the event "
            "format. Use S3/MinIO raw event-log delivery.", 422,
            {"source_type": source_type, "destination_provider": destination_provider},
        )


def require_table_management(source_type: str) -> None:
    if source_type in NEW_SOURCES and not source_capabilities(source_type)["incremental_snapshot"]:
        raise DomainError(
            "SOURCE_TABLE_OPERATION_UNSUPPORTED",
            "Online collection changes and resnapshot are not verified for this source; "
            "existing capture offsets will not be changed", 422,
        )


def source_topic(source, prefix: str, schema: str, collection: str) -> str:
    if source.type == "sqlserver":
        return f"{prefix}.{source.database_name}.{schema}.{collection}"
    return f"{prefix}.{schema}.{collection}"
