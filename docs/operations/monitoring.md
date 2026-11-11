# Monitoring

ClueCDC exposes `/health/live`, `/health/ready`, JSON structured request logs, correlation IDs, and Prometheus text metrics at `/metrics`. The UI separates platform, capture, delivery, and task-failure observations.

The included metrics are control-plane HTTP and explicitly collected runtime observations.
Monitoring and the Pipeline **Observability** tab read cached, versioned observations.
Connector lifecycle state is separate from telemetry availability. RUNNING does not
establish synchronization or destination correctness. Historical values remain
**Unavailable** without measurements; missing and stale values are never zero-filled.

## Trusted exporter setup

The optional `infrastructure/observability/compose.yaml` overlay exposes a real
JMX Java agent on Connect. Build the existing Connect image before building
`infrastructure/docker/observability-connect.Dockerfile`. The agent and checksum
are pinned; no Prometheus Server is required. Restrict exporter access to the
private collector network. The fixture publishes its metrics port on loopback only.

Set `OBSERVABILITY_EXPORTERS` through deployment administration as a JSON mapping:

```json
{"connect-cluster-uuid":{"actual-worker-id:8083":"http://trusted-worker:9404/metrics"}}
```

Use the actual task `worker_id` reported by Connect status; verify it against the
worker deployment identity. Include all workers that may host tasks. An arbitrary
hostname alias does not establish coverage. TLS certificate verification is enabled
for HTTPS exporters. `OBSERVABILITY_EXPORTER_TOKEN` optionally supplies a bearer
token through the deployment secret mechanism. Requests do not follow redirects or
inherit HTTP proxy settings. Public API requests cannot supply exporter URLs.

Rules in `infrastructure/observability/jmx-exporter.yaml` target Debezium 3.3.2.Final
and its bundled Connect 4.1.0. Exact attribute boundaries avoid matching averages,
maxima or duration attributes as counters or booleans. Custom Debezium MBean tags
require a reviewed exporter mapping; ambiguous or unmatched series remain unavailable.

`MilliSecondsBehindSource` describes the timestamp difference between the latest
source event and Debezium processing, including source/worker clock differences.
It is not destination latency. A gauge of -1 before the first streaming event is
inconclusive. `TotalNumberOfEventsSeen` is a connector counter; it does not prove
activity on an individual table. No heartbeat settings are changed by observation.

## Backlog, destination evidence and snapshots

Backlog uses targeted group descriptions, verified Connect task client identities,
topic metadata, earliest/end offsets and committed offsets. A group-name convention
is only a candidate. All required partitions must have consistent offsets before a
complete total is returned. Rebalances, membership changes, retention and offsets
outside the retained range invalidate complete totals. Offset distance is not an
exact count of business rows, particularly for compacted or transactional topics.

Consumer commits, sink processing/flush metrics and destination apply evidence are
separate. JDBC destination visibility is unsupported without an independent durable
marker. Warehouse evidence identifies only a delivery's persisted APPLIED or
CHECKPOINTED batch; it does not establish current row correctness. Experimental
providers remain experimental.

Snapshot metadata reuses PipelineTable and notifications. Duplicate/out-of-order
notifications cannot regress confirmed completion. Estimated rows are not used as
a percentage denominator. Snapshot stall findings additionally require fresh,
running, unpaused snapshot metrics and unchanged progress counters across the
configured window; notification silence alone is insufficient.

## Scheduling, history and alerts

The independent worker collector defaults to 60 seconds (allowed 30–300), four
concurrent pipelines, ten-second provider timeouts, a 25-second collection budget
and a 180-second freshness threshold. PostgreSQL row claims use leases and fencing;
the concurrency ceiling is shared across worker processes using a short advisory
transaction lock and database time. Remote I/O runs outside metadata transactions;
one pipeline/bucket is persisted at most once. Missed polls are skipped, and exporter,
Connect and Kafka failures use bounded backoff. Raw observability history is retained for seven days;
cleanup is limited to 1,000 rows per iteration and preserves legacy metric rows.

Authenticated readers with `pipelines.read` may use:

- `GET /api/v1/observability/summary` (bounded pagination, source/status filters)
- `GET /api/v1/pipelines/{id}/observability`
- `GET /api/v1/pipelines/{id}/observability/history` (timezone-aware range, at most seven days and 500 points)
- `GET /api/v1/pipelines/{id}/observability/capabilities`

History includes gaps and a truncation indicator. Narrow the range when the point
limit is reached. GET requests never invoke Kafka, Connect or exporter sampling.
The existing roles and navigation restrictions remain unchanged.

CDC alert events are `CDC_CAPTURE_LAG_HIGH`, `CDC_CONSUMER_BACKLOG_HIGH`,
`CDC_SINK_PROGRESS_STALLED`, `CDC_SNAPSHOT_PROGRESS_STALLED` and
`CDC_OBSERVABILITY_UNAVAILABLE`. An existing Admin alert rule must explicitly
select a CDC event type. Notification delivery also requires an enabled configured
channel. No notification rules are enabled by this feature.

Rules may add `config_json.observability` with `capture_lag_ms` (300000),
`backlog_records` (10000), `sink_stall_seconds` (300–3600) and
`snapshot_stall_seconds` (900–3600). Firing requires three valid consecutive
samples; recovery requires two. Sink stall evidence additionally spans at least
five minutes. Missing telemetry interrupts confirmation and never resolves an
active threshold incident. Existing cooldown and notification policies apply.

The private `/metrics` endpoint exposes persisted collector success/failure totals
as gauges for currently tracked pipelines (deleting a pipeline can reduce them),
latest collection duration, last-success timestamp and stale-pipeline count without
topic, table, error-message or arbitrary input labels. A successful lightweight
collection can still contain unavailable provider metrics; inspect metric status.

See the [v0.3.5 implementation and acceptance report](../development/v0.3.5-observability-implementation.md)
before considering a release. No automatic recovery is performed by observability.

In production, scrape the private API endpoint, Kafka brokers, Connect workers/JMX, database replication health, and destination systems. Alert on source-log retention risk, connector/task failure, Connect rebalances, Kafka disk/under-replication, consumer lag, and metadata database health.
