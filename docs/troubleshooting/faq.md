# Frequently asked questions

## Does ClueCDC move row data through its API?

No. Debezium, Kafka, and sink connectors form the data plane. The API manages configuration and operations.

## Which databases are supported?

PostgreSQL and MySQL are supported as both CDC sources and JDBC destinations.
Providers without working runtime adapters are not exposed.

## Why is monitoring data unavailable?

ClueCDC does not invent historical metrics. Configure external Kafka/Connect/database monitoring and a future metrics provider for time-series views.

## Does deleting a pipeline delete topics and destination rows?

No. External data and topics are preserved for safety. Operators must review cleanup separately.

## Is Docker Compose production-ready?

No. It is a loopback-only development and evaluation topology.
