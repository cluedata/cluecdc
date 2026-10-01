# Connector management

The Connect inventory and pipeline/delivery detail pages show connector class, desired state, worker-reported state, and tasks. Supported actions include pause, resume, task restart, validated configuration update, and deletion through resource workflows.

A successful REST response is not final runtime health. Refresh until every expected task is `RUNNING`, and inspect sanitized traces when a task is `FAILED`. In a distributed cluster, Connect may rebalance tasks after worker changes.
