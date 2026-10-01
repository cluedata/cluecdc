# Kafka Connect connections

Register the worker REST base URL visible to the API; local Compose uses `http://kafka-connect:8083`. ClueCDC lists installed plugins, validates configurations, deploys connectors, observes tasks, and performs pause, resume, restart, update, and delete operations.

Protect the REST API on a private network. The current connection model does not automate Connect REST authentication. The ClueCDC secret ConfigProvider also calls an internal API endpoint and requires a shared token of at least 32 characters.

All workers in a distributed cluster must use the same `group.id`, internal topics, plugin set, ConfigProvider configuration, and network reachability.
