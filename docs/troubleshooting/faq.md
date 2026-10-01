# Frequently asked questions

## Does ClueCDC move row data through its API?

No. Debezium, Kafka, and sink connectors form the data plane. The API manages configuration and operations.

## Can I use SQL Server or Trino?

No. SQL Server capture is planned but unimplemented. The current Iceberg integration uses a Hadoop catalog and does not manage Trino.

## Why is monitoring data unavailable?

ClueCDC does not invent historical metrics. Configure external Kafka/Connect/database monitoring and a future metrics provider for time-series views.

## Does deleting a pipeline delete topics and destination rows?

No. External data and topics are preserved for safety. Operators must review cleanup separately.

## Is Docker Compose production-ready?

No. It is a loopback-only development and evaluation topology.
