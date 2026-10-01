# Error recovery

Start from the failed component: source readiness, capture task, Kafka topic, sink task, or destination. Repair the underlying condition before retrying an operation or restarting a task.

Retries are explicit and audit-recorded. ClueCDC sanitizes task errors for display; use the correlation ID and private service logs when more detail is required. Do not delete offsets, topics, slots, or publications as a first response—those actions can turn a recoverable outage into data loss or duplicate replay.
