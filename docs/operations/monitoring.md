# Monitoring

ClueCDC exposes `/health/live`, `/health/ready`, JSON structured request logs, correlation IDs, and Prometheus text metrics at `/metrics`. The UI separates platform, capture, delivery, and task-failure observations.

The included metrics are control-plane HTTP and explicitly collected runtime observations. Historical throughput, lag, and freshness remain **Unavailable** when no metrics provider supplies them; the UI does not fabricate charts.

In production, scrape the private API endpoint, Kafka brokers, Connect workers/JMX, database replication health, and destination systems. Alert on source-log retention risk, connector/task failure, Connect rebalances, Kafka disk/under-replication, consumer lag, and metadata database health.
