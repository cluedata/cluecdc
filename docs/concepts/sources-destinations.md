# Sources and destinations

A **source** is a Connection with the SOURCE capability, used for database
discovery, readiness and capture. A **pipeline** selects tables and owns a
Debezium source connector. A **destination** is a Connection with the DESTINATION
capability. A **delivery** links pipeline topics to that target and owns an
independent Kafka Connect sink connector.

One capture pipeline can fan out to several deliveries. Pausing a delivery does not pause capture. Each delivery has independent consumer offsets, so a new delivery can replay retained Kafka history.

PostgreSQL/MySQL deliveries use JDBC table mappings. AWS S3/MinIO deliveries
archive CDC envelopes in JSONL files; they have topic selection, compression
and file settings, with no SQL table or primary-key options. Each delivery has
independent connector lifecycle and offsets.
