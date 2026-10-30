---
title: ClickHouse destination
description: Configure append-only CDC history delivery to ClickHouse.
---

# ClickHouse destination

ClueCDC uses the official Apache-2.0 ClickHouse Kafka Connect sink 1.7.0. Both
self-hosted ClickHouse and ClickHouse Cloud are configurable through the shared
connection and delivery workflows.

## Configuration

Provide host, HTTP(S) port, database, username, password, and TLS mode. TLS is
enabled by default for ClickHouse Cloud; local development can explicitly use
HTTP. **Test Connection** runs a read-only version/database query. Delivery
credentials remain encrypted and the generated connector refers to them
through the ClueCDC Kafka ConfigProvider.

With automatic creation enabled, each mapped topic receives a `MergeTree`
history table ordered by topic, partition, and offset. Its fixed columns are:

```text
_clue_topic, _clue_partition, _clue_offset, _clue_timestamp,
_clue_operation, _clue_key, _clue_before, _clue_after, _clue_source
```

Keys and Debezium documents are JSON strings, so composite keys are not joined
with an ambiguous delimiter. Kafka tombstones are skipped because the preceding
Debezium delete envelope is the authoritative history event.

## Supported mode and limits

Append-only history is implemented. The connector starts from the earliest
retained offsets for a new delivery and uses its own consumer group. v0.3.2 uses
at-least-once mode because the connector's `exactlyOnce=true` requires an
explicit Keeper/KeeperMap deployment and fails on a normal single-node server.
A replay can therefore add duplicate history rows; topic, partition, and offset
remain available for deterministic identification/deduplication.

Current-state replication is **NOT SUPPORTED** in v0.3.2. In particular,
`ReplacingMergeTree` is not an immediate SQL upsert and requires a proven
version/delete/query strategy. ClueCDC keeps that mode disabled until replay,
delete, restart, and destination-outage container tests pass.

The v0.3.2 local sink check passed on ClickHouse 26.8.20.9 for synthetic
Debezium create/update/delete envelopes, a composite key, Connect restart, and
a temporary destination outage. PostgreSQL/MySQL source-capture E2E remains
**NOT RUN** and production readiness remains false.

## Local test fixture

ClickHouse is an optional Compose profile and does not change the default stack:

```bash
docker compose --env-file .env.test.example \
  -f compose.yaml -f compose.test.yaml --profile clickhouse up -d clickhouse
```

Use `localhost:${CLICKHOUSE_TEST_PORT:-8123}` from the host or `clickhouse:8123`
inside Compose. The fixture is test-only; replace its public credentials before
using a shared environment.

## Troubleshooting

- `CLICKHOUSE_UNAVAILABLE`: verify host/port, TLS mode, and network policy.
- `CLICKHOUSE_AUTH_FAILED`: verify the user and password.
- `CLICKHOUSE_DATABASE_MISMATCH`: map tables to the configured database.
- `DESTINATION_TABLE_MISSING`: enable auto-create or create the fixed history
  schema before deployment.
- Connector discovery failures: rebuild the supplied Connect image and verify
  `com.clickhouse.kafka.connect.ClickHouseSinkConnector` appears in
  `/connector-plugins`.

