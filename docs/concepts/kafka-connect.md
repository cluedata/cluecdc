# Kafka Connect

Kafka Connect hosts Debezium source connectors and destination sink connectors in distributed mode. Its internal config, offset, and status topics are the runtime source of truth; ClueCDC stores desired resources and the last observed state.

The local Connect image includes the Debezium PostgreSQL and MySQL source
connectors, the Debezium JDBC sink, ClueCDC's secret ConfigProvider, and its
delivery transform. The API validates plugin availability before deployment.

In production, run multiple workers with the same group ID and internal topics. Tasks redistribute across workers. Never scale by creating independent Connect groups against the same internal topic names.
