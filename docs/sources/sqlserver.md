# SQL Server source setup

Use SQL Server 2017, 2019 or 2022 with a CDC-capable Standard, Enterprise or
Developer edition. Port defaults to 1433. Supply host, database, username,
encrypted password and timeout. TLS verifies the certificate by default;
`TrustServerCertificate` is never enabled implicitly. Import trusted CA roots
into both the API host and Connect JVM as appropriate.

The following scripts are **DBA setup examples**, never executed by readiness
checks. Review database/table eligibility, role membership and retention first.

```sql
USE [inventory];
EXEC sys.sp_cdc_enable_db;
EXEC sys.sp_cdc_enable_table
  @source_schema=N'sales', @source_name=N'orders',
  @role_name=N'cluecdc_reader', @supports_net_changes=0;
-- Grant SELECT on the captured source columns and access through the CDC gate role.
GRANT SELECT ON SCHEMA::sales TO [cluecdc];
ALTER ROLE [cluecdc_reader] ADD MEMBER [cluecdc];
GRANT VIEW DATABASE STATE TO [cluecdc];
-- SQL Server 2022 where required:
GRANT VIEW DATABASE PERFORMANCE STATE TO [cluecdc];
EXEC sys.sp_cdc_help_jobs;
-- DBA example only: choose retention longer than the maximum recovery window.
EXEC sys.sp_cdc_change_job @job_type=N'cleanup', @retention=10080;
```

Start SQL Server Agent outside ClueCDC. Inspecting `sys.dm_server_services`
requires the applicable server-state privilege (VIEW SERVER STATE, or VIEW
SERVER SECURITY STATE for newer releases); otherwise readiness reports unknown
visibility and blocks capture until requirements can be verified. `msdb` job
visibility and cleanup retention may require separate monitoring privileges.

Readiness checks version/edition, database CDC, SELECT/database-state permissions,
capture instances, Agent state and cleanup retention. Discovery uses `sys`
catalogs with bound parameters, retaining schemas such as dbo, sales and
inventory, composite keys, unique/filtered indexes, nullability and CDC status.
Captured columns must cover the table; incomplete capture instances block
deployment. Refresh capture instances deliberately after schema changes.

New topics follow Debezium's default **prefix.database.schema.table** contract.
PostgreSQL/MySQL names remain unchanged. Database names use `database.names` in
the SQL Server connector config. Schema history and durable LSN offsets remain
under Debezium/Kafka Connect ownership.

INT/BIGINT, BIT, DECIMAL/NUMERIC, Unicode character types, date/time, datetime2,
uniqueidentifier, binary and XML have explicit classifications. Unknown engine
types block capture rather than silently dropping columns. The live fixture
checks decimal/Unicode and CRUD, not every engine-specific type. Non-PK unique
identities are not assumed safe for stateful delivery.

Only raw S3/MinIO event-log delivery is enabled. Stateful JDBC/warehouse delivery
and online table add/remove/resnapshot are blocked pending verification. Shared
pause/resume/restart/monitoring remain available. If cleanup expires the saved
LSN, restore retained capture history or obtain approval for reconciliation;
ClueCDC does not reset LSNs or auto-resnapshot.

See [upstream SQL Server requirements](https://debezium.io/documentation/reference/3.3/connectors/sqlserver.html)
and [local evidence](../sources-extension-report.md).
