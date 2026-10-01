# PostgreSQL capture

Tested local source: PostgreSQL 17, Debezium 3.3.1.Final `pgoutput`, Kafka 4.1.2. Generated settings include an isolated slot/publication, filtered auto-publication, exact selected-table regexes, JSON converters without schema envelopes, heartbeat/batch/queue/poll configuration, and initial/no_data/always snapshots.

ClueCDC reads wal_level, version, replication capability, slot/sender capacity, available slots, and publication privileges. Discovery checks primary keys, SELECT privileges, and role ownership of selected tables. The MVP refuses deployment of selected tables with unresolved discovery issues. Readiness never executes ALTER SYSTEM, GRANT, or privileged remediation.

The replication user must be able to connect, SELECT the selected tables, use logical replication, create a publication, and own tables for filtered auto-publication. Pre-created externally managed publication/slot configuration is not yet exposed by the wizard.

PostgreSQL's default replica identity can omit non-key before values. The local sample initializes `REPLICA IDENTITY FULL`; on a real database an administrator must consider WAL overhead and explicitly configure identity if full before values are needed. ClueCDC does not apply it automatically.

Connector deletion leaves its logical slot/publication and Kafka topics. An inactive slot can retain WAL and fill disk. Inspect slots using an authorized DBA session:

```sql
SELECT slot_name, active, restart_lsn, confirmed_flush_lsn
FROM pg_replication_slots WHERE slot_name LIKE 'cluecdc_%';
SELECT pubname FROM pg_publication WHERE pubname LIKE 'cluecdc_%';
```

After confirming ownership, inactivity, and that no future resume is needed, a DBA can explicitly run `SELECT pg_drop_replication_slot('<reviewed slot>');` and `DROP PUBLICATION <reviewed publication>;`. These are manual destructive operations and are never automatic remediation. Deleting/recreating a prefix while retaining its old slot/offsets can resume earlier history; use a new prefix for a deliberately fresh capture.

Passwords are encrypted by the metadata secret provider. Connect configs hold unresolved references served through the bundled authenticated ConfigProvider. API outage does not proxy/pause ongoing CDC; restarting or revalidating a connector requires the secret resolver to be reachable.
