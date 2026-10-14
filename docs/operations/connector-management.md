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

Lifecycle mutations use an expiring metadata-backed operation lease. Concurrent
pause/resume/restart/delete requests for the same connector therefore return
`CONNECTOR_OPERATION_ACTIVE` instead of issuing reordered external calls. A
lease left by a crashed API expires after `CONNECTOR_OPERATION_LEASE_SECONDS`;
the next explicit request can then recover the operation. No database row lock
or transaction is held while waiting on Kafka Connect.

Deploy uses a deterministic ClueCDC connector identity. If an API crash occurs
after Kafka Connect accepted the connector but before metadata commit, retry
adopts it only when its complete raw configuration exactly matches the intended
configuration. A different configuration returns `CONNECT_DEPLOY_CONFLICT` and
is never overwritten or deleted. Compensation removes only an unowned connector
created by the current request.
