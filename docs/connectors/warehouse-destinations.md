---
title: Warehouse destinations
description: Capability and verification matrix for the ClueCDC v0.3.2 warehouse destinations.
---

# Warehouse destinations

ClueCDC v0.3.2 adds append-only CDC history delivery for ClickHouse, Google
BigQuery, Amazon Redshift, and Azure Synapse Dedicated SQL Pool. A history row
contains the Kafka topic, partition, offset and timestamp plus the Debezium
operation, key, before, after, and source documents. It is an event log, not a
current-state table.

The status terms below are deliberately separate from connection/UI support:

- **PASS**: the implementation was executed with the evidence named here.
- **NOT RUN**: implementation exists, but the required real service was not
  available in this checkout.
- **NOT SUPPORTED**: the UI and API reject the capability.
- **BLOCKED**: execution cannot proceed until the named external dependency is
  supplied.

## Capability matrix

| Capability | ClickHouse | BigQuery | Redshift | Azure Synapse |
| --- | --- | --- | --- | --- |
| Connection CRUD/UI | PASS | PASS | PASS | PASS |
| Mocked/unit connection test | PASS | PASS | PASS | PASS |
| Real connection test | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Append-only implementation | PASS | PASS | PASS | PASS |
| Initial snapshot events | PASS (design/unit) | PASS (design/unit) | PASS (design/unit) | PASS (design/unit) |
| Continuous INSERT/UPDATE/DELETE history | PASS (design/unit) | PASS (design/unit) | PASS (design/unit) | PASS (design/unit) |
| Keyless event-log mode | PASS | PASS | PASS | PASS |
| Composite key preservation | PASS (JSON encoding) | PASS (JSON encoding) | PASS (JSON encoding) | PASS (JSON encoding) |
| Retry/restart recovery | Connector-managed, at-least-once | Connector-managed | PASS (mock crash test) | PASS (mock crash test) |
| Real destination row verification | PASS (local 26.8 LTS) | NOT RUN | NOT RUN | NOT RUN |
| PostgreSQL/MySQL capture-to-warehouse E2E | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Current-state CDC/upsert/delete | NOT SUPPORTED | NOT SUPPORTED | NOT SUPPORTED | NOT SUPPORTED |
| Automatic schema evolution | NOT SUPPORTED; fixed history schema | NOT SUPPORTED; fixed history schema | NOT SUPPORTED; fixed history schema | NOT SUPPORTED; fixed history schema |
| Production-ready | No | No | No | No |

`PASS (design/unit)` means that snapshot and streaming Debezium envelopes use
the same append-only path and are covered by local tests. It does not mean a
real warehouse accepted the rows. See the
[implementation evidence](../development/v0.3.2-destinations.md) before a
production rollout.

The local ClickHouse PASS used the pinned Connect image and a real ClickHouse
26.8.20.9 server. It verified create/update/delete envelopes in target rows,
composite-key JSON, Connect restart, and recovery after a temporary destination
outage. Records were synthetic Debezium envelopes produced directly to Kafka;
therefore PostgreSQL/MySQL source capture E2E remains NOT RUN.

## Runtime architecture

ClickHouse and BigQuery use self-hostable Apache-2.0 Kafka Connect sinks and a
ClueCDC SMT that converts Debezium envelopes to the fixed history schema.
ClickHouse is pinned to `clickhouse-kafka-connect` 1.7.0; BigQuery is pinned to
the Aiven BigQuery connector 2.15.0.

Redshift and Synapse use managed staged runtimes inside the existing delivery
lifecycle:

```text
Debezium -> Kafka -> ClueCDC delivery -> object storage -> warehouse COPY
         -> target history table + destination batch marker -> Kafka checkpoint
```

Their durable ledger advances through `CREATED`, `STAGING`, `STAGED`,
`APPLYING`, `APPLIED`, and `CHECKPOINTED`. Kafka offsets advance only after the
warehouse transaction writes the target rows and destination marker. Replaying
a batch deduplicates by topic, partition, and offset. This is a replay-safe
protocol, not a claim of end-to-end exactly-once behavior.

## Current-state boundary

The four destinations reject `upsert` mode. ReplacingMergeTree, BigQuery MERGE,
Redshift MERGE, and Synapse MERGE are not enabled because replay ordering and
delete correctness have not passed their required real-destination matrices.
ClueCDC never silently converts a requested current-state delivery into an
append-only history delivery.

