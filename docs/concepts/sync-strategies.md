# Intelligent synchronization strategies

ClueCDC assesses every discovered table before it creates or changes a capture
pipeline. The assessment separates three decisions that have different safety
requirements:

- **Synchronization mode** describes the result: current state, append only, or
  a change-event log.
- **Effective key** describes source-row identity: primary key, enforced unique
  index, custom diagnostic candidate, or none.
- **Initial load** uses the existing Debezium snapshot modes. It is not a
  synchronization mode.

The backend assessment is authoritative. A connector task being `RUNNING` does
not prove that deletes, key changes, or destination uniqueness are correct.

## Modes

| Mode | Intended result | Row key | Initial delivery |
| --- | --- | --- | --- |
| Current State | One latest destination row per source row | Database-enforced PK or eligible UNIQUE key | PostgreSQL/MySQL JDBC sink |
| Append Only | Insert events; updates and deletes are not promised | Not required | Kafka capture or object storage |
| Change Event Log | Debezium `c`, `u`, `d`, and `r` envelopes when the source can emit them | Not required | Kafka capture, AWS S3, or MinIO |

Append-only delivery is at-least-once. Replays can produce duplicates, and a
keyless database sink is not described as idempotent. Change Event Log preserves
the existing CDC envelope; ClueCDC does not invent a source-row key from hashes,
timestamps, PostgreSQL `ctid`, row numbers, or Kafka offsets.

## Effective key resolution

Resolution is deterministic: primary key, then the narrowest eligible UNIQUE
index, then a user-requested diagnostic custom key, then no key. An automatically
eligible UNIQUE index must be valid, non-partial, non-expression based, contain
only real `NOT NULL` columns, and use supported key types.

Custom-key validation checks column existence, declared nullability, current NULL
values, duplicate combinations, type support, and existing uniqueness
enforcement. Queries have bounded timeouts and return no row values. Results are:

- `DATABASE_ENFORCED`: an eligible database constraint/index already guarantees
  the key.
- `VERIFIED_ONLY`: the current scan found no conflict, but future writes can
  violate the key. This does not enable production Current State Sync.
- `INVALID`: NULLs, duplicates, missing columns, or unsupported types were found.

ClueCDC can show source DDL as operator guidance but never runs that DDL.
Rediscover metadata after an operator applies a constraint or replication change.

## Supported matrix

| Source | Effective key | Source condition | Current State to PostgreSQL/MySQL | Kafka / S3 / MinIO event capture | Incremental snapshot |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL | Primary key | CDC readiness checks pass | Supported | Supported | Supported |
| PostgreSQL | Eligible UNIQUE index | `REPLICA IDENTITY FULL` | Supported | Supported | Unsupported; use initial snapshot or streaming-only |
| PostgreSQL | Eligible UNIQUE index | Replica identity is not `FULL` | Requires configuration | INSERT is available; full UPDATE/DELETE event identity is not advertised | Unsupported |
| PostgreSQL | Verified-only custom key | No enforced unique constraint | Unsupported | Supported only to the level reported by replica identity | Unsupported |
| PostgreSQL | No key | `REPLICA IDENTITY FULL` | Unsupported | Change Event Log or Append Only | Unsupported |
| PostgreSQL | No key | Default replica identity | Unsupported | Append Only; full UPDATE/DELETE support is not advertised | Unsupported |
| MySQL | Primary key | ROW binlog and CDC readiness checks pass | Supported | Supported | Supported |
| MySQL | Eligible UNIQUE index | `binlog_row_image=FULL` | Supported | Supported | Unsupported; use initial snapshot or streaming-only |
| MySQL | Eligible UNIQUE index | Row image is not `FULL` | Requires configuration | Capability is reported as conditional or unknown | Unsupported |
| MySQL | Verified-only custom key | No enforced unique constraint | Unsupported | Supported only to the level reported by binlog checks | Unsupported |
| MySQL | No key | FULL row image | Unsupported | Change Event Log or Append Only | Unsupported |

For Current State, JDBC delivery uses `upsert`, Kafka record keys, and delete
propagation. Auto-created destination tables receive a primary/unique constraint
matching the effective key. An existing target must already have compatible key
columns and enforcement; ClueCDC does not replace destination constraints.
Composite keys are supported when every component passes the same checks.

## Connector behavior

The source connector owns row identity for every delivery. ClueCDC compiles all
non-primary effective keys into one deterministic Debezium
`message.key.columns` value. Adding one table preserves mappings for every other
table in that connector.

The JDBC delivery transform reads `effective_keys` metadata and reconstructs the
record key with the existing logical-type rules. Metadata for older pipelines
still uses `primary_keys` and remains supported. Deletes and tombstones retain the
existing sink behavior, and transform errors do not include row contents.

Object-storage delivery does not use the JDBC transform. It retains the raw JSONL
CDC envelope. Object files can be replayed and duplicated, and ordering is only
meaningful within a Kafka partition. Do not assume Kafka key, partition, or offset
is persisted unless it is present in the inspected object format.

## Snapshots, recovery, and key changes

Initial blocking snapshots can capture existing rows for supported tables.
Incremental snapshot operations remain limited to primary-key tables because a
custom Kafka key does not establish safe Debezium chunking semantics.

An upsert resnapshot cannot remove a stale destination row that no longer exists
at the source. Complete reconciliation requires an explicit rebuild plan:

1. Create or choose an isolated destination table.
2. Keep the existing pipeline topics and offsets unchanged.
3. Run a supported initial load into the isolated target.
4. Validate counts and application-level invariants externally.
5. Perform an operator-controlled cutover.

ClueCDC does not truncate destination tables, delete Kafka topics, or reset source
offsets as part of resnapshot.

Changing an effective key changes Kafka record identity and can invalidate topic
history, destination constraints, pending events, and every attached delivery.
In-place changes are blocked. Create a new pipeline with a new topic prefix and
rebuild each destination explicitly.

## Drift and revalidation

Critical assumptions are revalidated during pipeline preview/creation, connector
deployment, delivery creation, add-table operations, and explicit metadata
rediscovery. A dropped UNIQUE index, altered key column, changed PostgreSQL
replica identity, changed MySQL row image, or incompatible destination constraint
blocks the affected new operation with a machine-readable error. Routine checks
do not scan entire tables for duplicates.
