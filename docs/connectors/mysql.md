# MySQL source and destination

ClueCDC supports MySQL 8 as both a Debezium CDC source and a JDBC destination. The same
pipeline can deliver PostgreSQL or MySQL topics to either PostgreSQL or MySQL. Database
credentials use the encrypted secret provider and are never returned by the API.

## Source prerequisites

Configure every MySQL server used for CDC with a non-zero server ID, binary logging, row
events, and full row images. The exact configuration mechanism varies by MySQL release and
managed service; confirm the variables after restart:

```sql
SHOW VARIABLES WHERE Variable_name IN
  ('server_id', 'log_bin', 'binlog_format', 'binlog_row_image', 'binlog_expire_logs_seconds');
```

A typical self-managed MySQL 8 configuration is:

```ini
[mysqld]
server-id=223344
log-bin=mysql-bin
binlog-format=ROW
binlog-row-image=FULL
binlog-expire-logs-seconds=604800
```

Choose binlog retention longer than the maximum connector outage. If Kafka Connect resumes
from an offset whose binlog was purged, the connector cannot silently reconstruct the gap.

Create a least-privilege CDC account. `LOCK TABLES` is needed when the deployment cannot use
a global read lock for the initial snapshot. ClueCDC's incremental snapshot signal table also
requires write/DDL access in the selected database:

```sql
CREATE USER 'cluecdc_cdc'@'10.%' IDENTIFIED BY '<generated-secret>' REQUIRE SSL;
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT
  ON *.* TO 'cluecdc_cdc'@'10.%';
GRANT CREATE, INSERT, UPDATE, DELETE ON shop.* TO 'cluecdc_cdc'@'10.%';
```

Use a network restriction appropriate for the Connect workers. Prefer TLS with hostname
verification in production. Managed services can use different parameter-group and
replication-role procedures; do not apply self-managed commands blindly.

## Readiness and discovery

`Test connection` validates connectivity, authentication, MySQL version, the selected
database, TLS, `log_bin`, `binlog_format`, `binlog_row_image`, and replication grants. Failed
checks include an expected value and recommended action. ClueCDC does not change server
configuration automatically.

Discovery normalizes a MySQL database as a namespace, then returns tables, ordered columns,
keys, indexes, estimated rows, and size through the same API used for PostgreSQL schemas.
Tables without primary keys remain selectable but show a warning; reliable upsert/delete and
incremental snapshot behavior requires a stable key.

## Connector identity, snapshots, and offsets

The MySQL builder configures `MySqlConnector`, an internal single-partition schema-history
topic, explicit database/table filters, precise decimals, delete tombstones, and source
signals. Each pipeline persists a unique `database.server.id`; connector restarts and table
updates reuse it. Connector names use immutable pipeline UUIDs. Kafka Connect offsets remain
the source of truth and are not reset for restart, pause/resume, metadata edits, or table adds.

| ClueCDC choice        | Debezium value | Behavior                                                        |
| --------------------- | -------------- | --------------------------------------------------------------- |
| Initial snapshot      | `initial`      | Consistent rows, then stream                                    |
| Schema only / no data | `no_data`      | Capture schema and stream without initial READ rows             |
| No snapshot           | `never`        | Begin at the current binlog position; use only when intentional |
| Always                | `always`       | Snapshot on each connector start                                |

Adding a table updates the filter in place and waits for the updated task to stabilize before
issuing Debezium's `execute-snapshot` signal for only the new table. Existing table offsets and
snapshots are preserved. Removing a table updates the filter without recreating the connector.

## MySQL destinations and cross-database mappings

Create a destination account with `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`,
`DROP`, and `INDEX` as required by the selected create/evolve options. Destination testing
reports each permission separately without mutating production tables.

Before deployment, ClueCDC maps source physical types to logical types and then to the target
dialect. Unsigned integers widen safely (`BIGINT UNSIGNED` becomes PostgreSQL
`NUMERIC(20,0)`), JSON maps to JSON/JSONB, and enum/set mappings produce warnings. Spatial or
unknown types are incompatible until an explicit safe mapping is added.

Delete propagation is explicit (`delete.enabled=true`) and uses Debezium delete envelopes;
the ClueCDC transform drops the following Kafka tombstone rather than treating it as the
database delete. Use upsert with record-key primary keys for at-least-once delivery.

## Local development

Compose exposes the CDC-ready source at host port `3307` and the destination at `3308`.
Containers use `cdc-source-mysql:3306` and `destination-mysql:3306`. Bootstrap generates
private passwords in `.env`. The source database `shop` contains 100 UTF-8/emoji-aware
customer, order, and order-item fixtures. The destination database is `analytics_mysql`.

## Troubleshooting

- Failed binlog check: enable binary logging, use `ROW` and `FULL`, then restart and retest.
- `SERVER_ID_CONFLICT`: choose another Source base ID; deployed pipeline IDs remain stable.
- `DATABASE_NOT_FOUND`: verify the database exists and the account can see it.
- `SSL_ERROR`: verify the CA chain, hostname, and MySQL TLS policy.
- Connector cannot resume: check whether required binlogs were purged; do not delete/recreate
  the connector as a default recovery action.
- Destination task fails on a column: rerun discovery and compatibility preview; ClueCDC does
  not silently truncate unsupported values.
