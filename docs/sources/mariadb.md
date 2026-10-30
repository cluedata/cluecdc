# MariaDB source setup

Use MariaDB 11.4.x or 11.7.x with the dedicated 3.3.2.Final MariaDbConnector.
MariaDB remains a distinct connection type. MySQL discovery is reused only
where compatible; MariaDB index queries exclude prefix indexes and avoid the
MySQL-only statistics expression column. Custom scans use `max_statement_time`.

Example server configuration, applied by your DBA:

```ini
[mariadb]
server_id=445566
log_bin=mariadb-bin
binlog_format=ROW
binlog_row_image=FULL
binlog_legacy_event_pos=ON
log_bin_compress=OFF
expire_logs_days=7
character_set_server=utf8mb4
```

Review capacity and maximum outage before choosing retention. Readiness reports
server identity, ROW/FULL settings, retention, GTID strict/domain settings and
replication permissions. Replicated topologies and GTID failover remain
unverified; standalone fixture results do not establish replica compatibility.

DBA example, replacing host/account/password and limiting privileges to your
approved capture scope where possible:

```sql
CREATE USER 'cluecdc'@'worker-host' IDENTIFIED BY 'replace-with-secret';
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, BINLOG MONITOR,
  LOCK TABLES ON *.* TO 'cluecdc'@'worker-host';
```

MariaDB is source-only here. Port defaults to 3306; TLS is enabled and certificate
verification is required by default. The installed MariaDB JDBC connector uses
`database.ssl.mode=verify-full` or `disable`, as verified through runtime
configuration validation. MySQL's existing TLS settings are untouched. API and
Connect workers both need trusted CA roots. Test fixtures explicitly disable TLS.

Database/table identity and composite indexes are retained. PKs and non-null
full-value unique indexes can identify current-state rows when binlog images
are FULL. Nullable, expression and prefix indexes are rejected. Custom keys are
scanned for NULL/duplicates; verified-only uniqueness is insufficient. Each
capture obtains a persisted unique replication server ID without changing old
MySQL allocations. Connector history and topic prefix remain stable on restart.

Numeric/decimal, Unicode text, binary, date/timestamp, ENUM/SET metadata reuse
the existing logical mapper. MariaDB JSON aliases remain text when the catalog
reports LONGTEXT; no false native-JSON assumption is made. Spatial values require
raw event delivery. Negative/long TIME durations and unusual temporal semantics
need separate stateful-destination verification. Decimal precision and Unicode
are checked in the live fixture; the full type matrix is not certified.

Initial snapshot/streaming use the shared orchestrator. PK incremental snapshots
and table changes reuse MySQL signal services, conditionally requiring CREATE
and INSERT privileges for the pipeline-specific signal table. Never grant those
privileges automatically. Adding a table does not reset captured offsets.

On a transient outage, let Debezium reconnect or restart the failed task through
ClueCDC. For purged binlogs, restore the required history or approve a separate
destination reconciliation plan. Do not reset offsets or switch to an automatic
snapshot mode to hide missing history.

See [upstream MariaDB configuration](https://debezium.io/documentation/reference/3.3/connectors/mariadb.html)
and [local evidence](../sources-extension-report.md).
