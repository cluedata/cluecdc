# Kafka Connect errors

Call the worker `/connector-plugins` endpoint and confirm the expected Debezium
source and JDBC sink classes are installed on every worker. Inspect connector
status, each task trace, worker logs, internal-topic permissions, group
rebalances, and the ClueCDC secret-provider endpoint.

All distributed workers need identical plugin directories and configuration. A connector can be `RUNNING` while one task is `FAILED`; always inspect tasks.
