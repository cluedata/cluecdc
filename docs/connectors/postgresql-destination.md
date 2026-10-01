# PostgreSQL destinations

A Destination represents a logical PostgreSQL target with an encrypted secret reference. Each PipelineDestination is an independently managed delivery from an existing capture pipeline, with its own real sink Connector, mappings, desired/actual state, and consumer offsets. Capture continues when a sink pauses or fails. Multiple pipelines can share a destination; conflicting target tables within that destination are rejected. Separate logical destinations can still point at the same database, so operators must avoid unintended cross-destination table collisions.

## Installed plugin and record format

The pinned `quay.io/debezium/connect:3.3.1.Final` image includes `io.debezium.connector.jdbc.JdbcSinkConnector` and its PostgreSQL JDBC driver. The API checks the selected cluster's actual `/connector-plugins` response before deployment and then invokes Connect config validation. Missing plugins produce `SINK_PLUGIN_MISSING`; neither a delivery association nor an assumed healthy connector is created.

The existing capture connectors emit schemaless JSON. JDBC consumes typed Kafka Connect Structs. The Connect image compiles and installs `io.cluecdc.connect.ClueDeliveryTransform`, a sink-only transformation that reconstructs supported types from discovered source metadata and maps source schema/table fields to the chosen target. Existing capture configuration and Kafka records remain intact. Keys become typed record-key Structs. DELETE envelopes perform deletes when enabled; null tombstones are ignored. No row contents are sent to the control-plane database or included in transformation errors.

Native connector behavior is described by the [Debezium JDBC documentation](https://debezium.io/documentation/reference/3.3/connectors/jdbc.html). This release uses PostgreSQL, record-key primary keys, upsert by default, optional insert mode, independent consumer groups, and explicit mappings. INSERT mode can fail on replay; it does not offer exactly-once writes.

## Validation and schema policy

Preview and deployment check the deployed capture pipeline, matching Kafka cluster, existing Kafka topics, discovered source columns and primary keys, supported types, target connectivity, schema USAGE/CREATE permissions, table INSERT/UPDATE/DELETE privileges, table ownership for evolution, matching primary-key columns, and missing columns when evolution is disabled. Connect validation must also succeed. Pre-existing column type compatibility, extra required columns, checks, triggers, and database-specific policies can still cause runtime failures; validation is not a guarantee of successful writes.

Supported discovered types are bigint/integer/smallint, real/double precision, boolean, text/character types, numeric with declared precision and scale, UUID, JSON/JSONB, bytea, date, and timestamps with/without time zone. Decimal bytes are converted using their declared scale. Arrays, enums, unbounded numeric, time/interval and other types are rejected rather than guessed. An unavailable TOAST placeholder is rejected to prevent unsafe overwrites. Source schema changes require discovery followed by a validated mapping update; unexpected record fields fail safely.

| Auto create | Auto evolve | Behavior                                                                               |
| ----------- | ----------- | -------------------------------------------------------------------------------------- |
| On          | On          | Native JDBC `schema.evolution=basic` creates missing tables and adds supported columns |
| On          | Off         | Deployment explicitly creates missing tables from validated metadata; JDBC uses `none` |
| Off         | Off         | Existing compatible tables are required; JDBC uses `none`                              |
| Off         | On          | Rejected because native JDBC combines creation and evolution                           |

The API never creates schemas. Table provisioning occurs only on deployment or a mapping update with create-only enabled, never during preview. Tables are independent and have primary keys; source foreign keys and defaults are not copied. Native evolution does not migrate arbitrary type changes or remove columns, and adding a required column to populated tables can fail. Review database changes before enabling evolution.

New deliveries consume from the earliest retained offsets. That cannot recover source history already removed from Kafka. Changing a mapping retains offsets and writes subsequent records to the new target; it does not backfill earlier records. Explicit historical backfill/reset is outside this release.

## Runtime, incidents and lifecycle

Connect's actual connector and task states are authoritative. A RUNNING connector with a failed task is DEGRADED; unreachable Connect is UNKNOWN; PAUSED is neutral. Desired state remains separately visible. Polling records audit entries only on state changes. Incidents are associated with destination, pipeline and connector, deduplicated while open, and resolved on observed recovery. Operators can acknowledge or resolve incidents in Errors. Sanitized task hints expose failure categories without raw connector traces or row payloads.

RUNNING does not establish a successful database write. In JDBC 3.3, a first processing exception can be retained until the next input batch causes Connect to publish task failure; reconciliation reports the state actually returned by Connect. The real failure acceptance sends a subsequent change, observes FAILED/DEGRADED, removes the blocking target constraint, restarts the task, verifies the row in PostgreSQL, and confirms incident resolution. Throughput, lag and last successful write remain unavailable without a metrics provider.

Pause/resume/restart can apply to all deliveries on a destination or one delivery using `delivery_id`. Task restart is independently available. Partial multi-delivery failures report the deliveries already completed. Connection edits and deletion are blocked until dependent deliveries are removed; descriptive edits remain possible. Delivery removal deletes the runtime connector and association, retaining target tables and data. Capture deletion is blocked while deliveries depend on it.

Secrets use the existing encrypted metadata provider. Stored Connect passwords are unresolved `${cluecdc:<uuid>:password}` references; previews, ordinary APIs, audits and UI configuration are redacted. The API-to-Connect transaction cannot be atomic: it uses row locks, preflight, non-overwriting connector creation, and compensation after metadata failure. A process crash, ambiguous external timeout, failed compensation or already committed target DDL may require operator reconciliation. Production requires private authenticated Connect access and reviewed permissions, retention, backup and capacity settings.

## Real local acceptance

Run `python scripts/demo-destination.py` after Compose starts. It establishes PostgreSQL capture, deploys two real JDBC sinks into the independent `destination-postgres` database, and checks fresh inserts, updates including decimals/timestamps, deletes, independent pause/catch-up, restart, and audits. The result is written to `artifacts/destination-demo-result.json`. Browser acceptance additionally exercises the destination wizard, create-only tables, mapping changes, edits, task restart, invalid credentials, incident acknowledgement/resolution, real write failure and recovery. Fixtures are cleaned while target data for the two demo destinations remains available.
