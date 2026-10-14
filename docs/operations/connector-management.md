# Connector management

The Connect inventory and pipeline/delivery detail pages show connector class, desired state, worker-reported state, and tasks. Supported actions include pause, resume, task restart, validated configuration update, and deletion through resource workflows.

A successful REST response is not final runtime health. Refresh until every expected task is `RUNNING`, and inspect sanitized traces when a task is `FAILED`. In a distributed cluster, Connect may rebalance tasks after worker changes.

Desired state records operator intent; observed state records what Kafka
Connect last reported. When Connect REST is unavailable, observed state becomes
`UNKNOWN`, but a user-requested `PAUSED` or `STOPPED` state remains unchanged.
Recovery reconciliation never recreates a missing connector or resets offsets
implicitly. Treat a missing connector, unknown offsets, or exhausted recovery
as an operator-visible incident and follow the documented explicit recovery
procedure.

Background reconciliation isolates failures per pipeline and delivery. One
unreachable or malformed resource therefore does not prevent other resources
or snapshot notifications from being processed during the same worker cycle.
