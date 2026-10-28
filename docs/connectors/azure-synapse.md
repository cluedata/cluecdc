---
title: Azure Synapse Analytics destination
description: Configure and validate an Azure Synapse Dedicated SQL Pool and Azure Storage staging connection.
---

# Azure Synapse Analytics destination

ClueCDC v0.3.2 recognizes `azure_synapse` / `AZURE_SYNAPSE` as a
destination-only connection for **Azure Synapse Dedicated SQL Pool**. Serverless
SQL Pool is intentionally rejected: it has no mutable in-database tables and is
not a stateful CDC target.

!!! warning "Delivery status"
    Connection CRUD, secret handling, the UI form, Dedicated SQL Pool checks,
    storage checks, type mapping, COPY SQL generation, and durable batch-state
    primitives are implemented. The Kafka-to-Synapse staged delivery worker has
    not passed real Azure cloud tests and is therefore disabled. Stateful CDC is
    not enabled. See the [v0.3.2 status](../development/v0.3.2-destinations.md).

## Architecture and supported modes

The intended open-source data path is:

```text
Source -> Debezium -> Kafka -> ClueCDC delivery -> Azure Storage
       -> Synapse COPY INTO -> staging table -> batch apply -> target table
```

Azure Data Factory, Synapse Pipelines, Airbyte, and Confluent's commercial
connector are not required. Confluent's Azure Synapse sink is an optional
external integration only: it is commercially licensed and insert-only, so it
cannot provide ClueCDC stateful CDC.

| Mode | Status | Notes |
| --- | --- | --- |
| Append-only | BLOCKED | SQL/COPY primitives are unit-tested; runtime and Azure cloud execution are not complete. |
| Stateful CDC | NOT SUPPORTED | Disabled until UPDATE/DELETE/INSERT or MERGE behavior is verified on Dedicated SQL Pool. |

`MERGE` is preview functionality in Dedicated SQL Pool and has distribution and
version caveats. ClueCDC does not assume SQL Server MERGE semantics and does not
silently downgrade stateful CDC to append-only.

## Connection configuration

Create a destination from **Destinations -> New Destination -> Azure Synapse
Analytics**.

SQL settings require the workspace name, dedicated endpoint (normally
`workspace.sql.azuresynapse.net`), SQL pool database, schema, and port 1433.
TLS and certificate verification are mandatory. An endpoint containing
`-ondemand.sql.azuresynapse.net` is rejected.

Authentication supports SQL username/password and Microsoft Entra service
principals. Managed identity is accepted only when the deployment explicitly
sets `AZURE_MANAGED_IDENTITY_ENABLED=true`; it is not shown by default. Tenant
and client fields are hidden for SQL authentication.

Staging requires an Azure Storage account, Blob or ADLS Gen2 container, and a
non-empty prefix. Account key, SAS, service-principal, and deployment-enabled
managed-identity authentication are modeled. Storage operations are confined to
the configured prefix.

Advanced settings include batch size, flush interval, concurrency, bounded
retry/backoff, statement timeout, staging retention, and `ROUND_ROBIN`, `HASH`,
or `REPLICATE` distribution. `ROUND_ROBIN` is the safe default. An effective row
key is not automatically selected as the distribution key.

## Connection test

The test performs no production-table DDL or DML. It:

1. opens an encrypted SQL connection;
2. verifies engine edition 6 and the requested database;
3. requires an online pool and existing schema;
4. checks read, insert, and table-creation permissions;
5. writes, reads, and deletes a uniquely named probe below
   `<prefix>/.cluecdc/connection-tests/`.

SQL connectivity/authentication errors and Azure Storage permission errors have
different error codes. Error 42108 is reported as a paused pool. ClueCDC never
resumes paid compute automatically.

## Least-privilege guidance

The SQL principal needs `CONNECT`, schema visibility, `SELECT`, `INSERT`, and
permission to create staging tables for append-only loading. A future stateful
apply also needs `UPDATE` and `DELETE`; do not grant these merely to make the
current connection test pass. Auto-create needs `CREATE TABLE` and schema
control appropriate to the target schema. Synapse primary-key constraints are
not generated or treated as enforced.

The storage identity needs container/list access and create/read/delete access
only beneath the configured staging prefix. Prefer Entra identities over shared
keys where the deployment supports them. Passwords, client secrets, account
keys, and SAS tokens are encrypted by ClueCDC, masked in API responses, and are
never included in generated SQL or logs.

## Type and schema rules

ClueCDC maps booleans to `bit`, integral types to a non-narrowing Synapse
integer/decimal type, decimal values to `decimal(p,s)` up to precision 38,
timestamps to `datetime2(6)` or `datetimeoffset(6)`, Unicode text/JSON to
`nvarchar`, and bytes to `varbinary`. Precision above 38 is rejected. Unbounded
values use explicit MAX types rather than silent truncation.

Automatic evolution is additive only. It must not drop columns, narrow or
replace types, rebuild a production table, change a distribution key, or remove
dependent objects. Existing distribution strategies are never changed
automatically.

## Recovery model

The metadata model records `PLANNED`, `STAGED`, `COPIED`, `APPLYING`, `APPLIED`,
and `FAILED` independently, along with delivery/batch IDs, Kafka offset ranges,
the staging manifest, retries, and completion time. A checkpoint is eligible to
advance only after `APPLIED`; an upload or COPY alone is insufficient. Staging
cleanup is safe only after durable apply success.

This design targets replay-safe batches, not an unverified exactly-once claim.
Bounded exponential retries should cover transient SQL throttling, token expiry,
network failures, and storage outages while isolating unrelated deliveries.

## Cloud testing

Cloud tests are opt-in and must point at operator-provisioned resources. They
must never provision or resume paid Azure resources. Until PostgreSQL and MySQL
source matrices pass snapshot, CRUD, key change, composite/custom keys, replay,
outage, staging failure, schema evolution, concurrency, and each supported
distribution strategy, record cloud execution as **NOT RUN** rather than using
mock tests as proof.

## Troubleshooting

- `AZURE_SYNAPSE_DEDICATED_REQUIRED`: the endpoint is not a Dedicated SQL Pool.
- `AZURE_SYNAPSE_POOL_PAUSED`: resume the pool explicitly in Azure, then retest.
- `AZURE_SYNAPSE_AUTH_FAILED`: verify the SQL principal or Entra database user.
- `AZURE_SYNAPSE_PERMISSION_DENIED`: grant the documented SQL permissions.
- `AZURE_STORAGE_PERMISSION_DENIED`: verify the container role/SAS scope and prefix.
- `AZURE_STORAGE_TEST_CLEANUP_FAILED`: delete the probe and correct delete permission.
- `AZURE_SYNAPSE_DELIVERY_NOT_VALIDATED`: connection management is available, but delivery remains gated.

Microsoft references: [Synapse T-SQL feature matrix](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql/overview-features),
[Dedicated SQL Pool authentication](https://learn.microsoft.com/en-us/azure/synapse-analytics/sql-data-warehouse/sql-data-warehouse-authentication),
[COPY INTO](https://learn.microsoft.com/en-us/sql/t-sql/statements/copy-into-transact-sql),
and [MERGE](https://learn.microsoft.com/en-us/sql/t-sql/statements/merge-transact-sql).

