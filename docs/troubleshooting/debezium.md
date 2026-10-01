# Debezium errors

Open the capture connector and task status. Common causes are missing replication privileges, unavailable WAL/binlogs, conflicting slot/server identity, publication ownership, tables without stable keys, schema-history topic access, or an invalid snapshot signal table.

Repair the source condition, then restart the failed task. Avoid deleting offsets or replication slots unless you have designed a new snapshot/replay boundary and understand duplicate or missing-data risk.
