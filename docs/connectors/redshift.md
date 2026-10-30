---
title: Amazon Redshift destination
description: Configure S3-staged append-only CDC history delivery to Amazon Redshift.
---

# Amazon Redshift destination

Amazon Redshift uses ClueCDC's managed staged runtime rather than a second
orchestration system:

```text
Kafka -> CSV + Redshift manifest in S3 -> COPY staging table
      -> transactional target inserts + destination batch marker -> checkpoint
```

Provisioned clusters and Redshift Serverless are modeled. Provide host, port,
database, schema, username, region, S3 bucket/prefix, TLS, and either password
or IAM database authentication. COPY can use a configured IAM role, explicit
encrypted temporary/static AWS credentials, or the deployment's default role.

**Test Connection** verifies SQL connectivity, schema `USAGE`/`CREATE`, and a
prefix-scoped S3 write/head/delete probe. It does not create a production user
table. Restrict the identity to the configured bucket prefix and required
Redshift schema.

## Recovery behavior

The staging object, Redshift manifest, and ClueCDC manifest are durable. COPY
loads a per-delivery staging table. Target inserts deduplicate by Kafka topic,
partition, and offset; the same transaction records a destination batch marker.
Only a verified marker allows the Kafka checkpoint. A crash after apply but
before checkpoint therefore resumes without applying the target batch twice.

Append-only history is implemented. Redshift MERGE/current-state CDC and
automatic source-schema evolution are **NOT SUPPORTED** until real Redshift
replay and outage tests pass. S3 cleanup is operator-managed in v0.3.2; use a
bucket lifecycle rule scoped to the configured staging prefix.

## Troubleshooting

- `REDSHIFT_UNAVAILABLE`: verify endpoint, paused/resuming state, routing, and
  TLS access.
- `REDSHIFT_PERMISSION_DENIED`: verify both database and S3/COPY permissions.
- `REDSHIFT_SCHEMA_MISMATCH`: map every topic to the configured schema.
- A batch in `RETRYABLE_FAILED` is retried by reconciliation; a
  `TERMINAL_FAILED` batch requires correcting the configuration/permissions and
  operator intervention.

