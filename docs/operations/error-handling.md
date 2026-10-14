# Error handling

API errors use `{"error":{"code","message","details"}}`. Validation responses include safe field locations and error types but omit supplied secret values. Unexpected failures return a correlation ID; server logs hold structured context without request bodies or authorization headers.

Operational incidents are durable records that can be acknowledged and resolved. Acknowledging documents investigation; it does not repair the connector. Runtime recovery can resolve matching active incidents, while notification delivery failures remain separate from pipeline lifecycle success.

Worker jobs classify failures as transient connectivity, temporary resource
exhaustion, authentication, invalid configuration, schema incompatibility,
missing source history, or unknown. Only retryable categories receive automatic
bounded exponential backoff with jitter. Authentication, configuration, schema,
and missing WAL/binlog/offset history failures stop immediately; correct the
root cause and request a manual retry. ClueCDC never resets offsets or recreates
a connector as an implicit retry.

`GET /api/v1/jobs/{id}` exposes the current error code/category, retryability,
attempt count, last/next attempt timestamps, and a bounded failure history.
`POST /api/v1/jobs/{id}/retry` starts a fresh bounded attempt cycle for a failed
job while preserving that history. Connector-task traces are sanitized and
retained in connector runtime/alerts; task or connector restart remains an
explicit operator action so permanent failures cannot form restart loops.
