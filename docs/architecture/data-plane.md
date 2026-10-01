# Data plane

PostgreSQL WAL is decoded with `pgoutput`. Debezium performs the configured snapshot, streams changes, and maintains Kafka Connect offsets. Kafka Connect config/status/offset topics remain its state store. ClueCDC stores desired configuration and runtime observations, never row history or Kafka offsets.

Each topic prefix maps deterministically to one connector name and a SHA256-derived PostgreSQL slot/publication name. Table names are escaped as exact regular expressions in `table.include.list`. Publication creation is filtered to the selected tables; this MVP requires selected-table ownership and stable primary keys. Deleting a pipeline removes its connector but preserves topics, slots, and publications.

Topic inspection uses Kafka admin metadata. Event inspection creates a consumer without a group, disables commits, assigns partitions explicitly, captures end offsets, and samples a recent bounded window. Bounds: 200 returned events, 1,000 scanned records, 32 partitions, 2 MB of scanned payload/key bytes, four-second polling window, twelve-second total timeout. Filters operate inside that window. Concurrent requests are limited by an API semaphore. No historical scan or permanent buffering exists.

State derives from Connect: connector FAILED → FAILED; connector RUNNING plus a failed task → DEGRADED; connector PAUSED → PAUSED; RUNNING with all observed tasks running → RUNNING; no tasks, malformed response, missing connector, or unreachable worker → UNKNOWN. Desired state is never overwritten from these observations.

Stream/snapshot measurements require future Prometheus/JMX providers. Null values are deliberate, not zero estimates. Application request counters are available independently on `/metrics`.

PostgreSQL destinations consume Kafka through real Debezium JDBC sink connectors. Each delivery has independent Connect consumer offsets and lifecycle. The included sink-side transformation restores typed records from discovered metadata and applies explicit schema/table mappings without altering capture events. New deliveries start at the earliest retained offsets; mapping updates retain offsets. Upsert is the default, deletes use CDC envelopes, and tombstones are skipped. Sinks never read source databases for row transport. See [destination operations](../connectors/postgresql-destination.md) for supported types, creation/evolution, runtime failure reporting and limits.

Upstream references: [Debezium PostgreSQL connector](https://debezium.io/documentation/reference/3.3/connectors/postgresql.html), [Kafka configuration providers](https://kafka.apache.org/42/configuration/configuration-providers/), and [Kafka Docker](https://kafka.apache.org/41/getting-started/docker/).
