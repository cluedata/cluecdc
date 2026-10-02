# Sources and destinations

A **source** is a database connection used for discovery, readiness, and capture. A **pipeline** selects tables and owns one Debezium source connector. A **destination** is a reusable target connection. A **delivery** links pipeline topics to destination tables and owns an independent sink connector.

One capture pipeline can fan out to several deliveries. Pausing a delivery does not pause capture. Each delivery has independent consumer offsets, so a new delivery can replay retained Kafka history.

Destinations are PostgreSQL or MySQL connections consumed by managed JDBC sink
connectors. Each delivery has independent connector lifecycle and offsets.
