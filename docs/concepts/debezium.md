# Debezium

ClueCDC builds and validates Debezium source connector configurations for PostgreSQL and MySQL. It derives deterministic connector identifiers, exact table filters, topic prefixes, converter settings, heartbeat/batch options, and signal support.

Connector validation happens through Kafka Connect before creation. Immutable identity settings—such as PostgreSQL slot/publication names, MySQL server ID, topic prefix, and schema-history topic—are protected from unsafe in-place changes.

Debezium runs inside Kafka Connect. ClueCDC does not fork Debezium or proxy CDC records. Consult the installed connector's status and task trace when capture fails.
