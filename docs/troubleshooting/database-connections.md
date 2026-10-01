# Database connection issues

- Use the hostname reachable from the API container or pod, not your workstation's `localhost`.
- Confirm port, database, user, password, DNS, routing, and firewall independently.
- For PostgreSQL, inspect `pg_hba.conf`, TLS mode, `wal_level`, replication grants, table ownership, and primary keys.
- For MySQL, inspect bind address, grants, row-based binlog settings, server ID, and binlog retention.
- Re-run the ClueCDC connection test after repair; credentials remain write-only, so submit a new password when rotating it.

Connection health does not prove CDC readiness. Run discovery and the provider-specific readiness check.
