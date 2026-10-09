# Data plane

PostgreSQL WAL is decoded with `pgoutput`; MySQL changes are read from row-based
binary logs with full row images. Debezium performs the configured snapshot,
streams changes, and maintains Kafka Connect offsets. Kafka Connect
config/status/offset topics remain its state store. ClueCDC stores desired
configuration and runtime observations, never row history or Kafka offsets.

Each topic prefix maps deterministically to one connector name. PostgreSQL uses
SHA256-derived slot/publication names and a filtered publication for selected
tables. MySQL pipelines persist a unique Debezium server ID. Table names are
escaped as exact regular expressions in `table.include.list`. Stable primary
keys are required for reliable updates, deletes, and incremental snapshots.
Deleting a pipeline removes its connector but preserves topics and source-side
replication artifacts.

Topic inspection uses Kafka admin metadata only. The former `/events` payload
endpoint returns HTTP 410: CDC records must not pass through the control-plane
API. Inspect records directly in Kafka or destination objects. Test-only direct
Kafka verification is bounded and never commits offsets; the worker's bounded
consumer reads Debezium snapshot control notifications, not table CDC records.

State derives from Connect: connector FAILED → FAILED; connector RUNNING plus a failed task → DEGRADED; connector PAUSED → PAUSED; RUNNING with all observed tasks running → RUNNING; no tasks, malformed response, missing connector, or unreachable worker → UNKNOWN. Desired state is never overwritten from these observations.

Stream/snapshot measurements require future Prometheus/JMX providers. Null values are deliberate, not zero estimates. Application request counters are available independently on `/metrics`.

PostgreSQL and MySQL database destinations consume Kafka through real Debezium
JDBC sink connectors. Each delivery has independent Connect consumer offsets
and lifecycle. The included sink-side transformation restores typed records
from discovered metadata and applies explicit schema/table mappings without
altering capture events. New deliveries start at the earliest retained offsets;
mapping updates retain offsets. Upsert is the default, deletes use CDC
envelopes, and tombstones are skipped. Sinks never read source databases for
row transport. See [PostgreSQL destination](../connectors/postgresql-destination.md)
and [MySQL](../connectors/mysql.md) for supported types, creation/evolution,
runtime failure reporting, and limits.

AWS S3 and MinIO destinations consume the same topics through the Aiven S3 sink
and write raw Debezium envelopes as gzip or plain JSONL objects. They preserve
capture semantics rather than converting events into relational rows. See
[AWS S3 and MinIO destinations](../connectors/object-storage.md).

Upstream references: [Debezium PostgreSQL connector](https://debezium.io/documentation/reference/3.3/connectors/postgresql.html), [Kafka configuration providers](https://kafka.apache.org/42/configuration/configuration-providers/), and [Kafka Docker](https://kafka.apache.org/41/getting-started/docker/).
