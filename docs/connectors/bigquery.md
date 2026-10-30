---
title: Google BigQuery destination
description: Configure append-only CDC history delivery to Google BigQuery.
---

# Google BigQuery destination

ClueCDC uses the self-hostable Apache-2.0 Aiven BigQuery Kafka connector 2.15.0
and its Storage Write API path. No managed Confluent connector is required.

## Configuration and permissions

Provide the Google Cloud project, dataset, dataset location, and one of:

- a service-account JSON credential, stored encrypted; or
- Application Default Credentials (ADC) supplied by the deployment, including
  workload identity where the platform supports it.

**Test Connection** verifies authentication, dataset access/location, and the
ability to submit a dry-run query. It does not create a user table. The identity
also needs the permissions required by the Storage Write API and table creation
when automatic creation is enabled.

The connector maps topics explicitly to tables, writes the fixed history
columns documented in the [warehouse overview](warehouse-destinations.md), and
preserves Kafka topic/partition/offset metadata. New fields and required-field
relaxation are disabled; all history fields that can be absent are nullable.

## Supported mode and limits

Append-only event history is implemented. Current-state MERGE, delete
application, and automatic source-schema evolution are **NOT SUPPORTED**. A
successful API/unit test does not prove real BigQuery delivery: service-account
or workload-identity validation and PostgreSQL/MySQL E2E remain **NOT RUN** in
the default test suite.

## Troubleshooting

- `BIGQUERY_CREDENTIALS_INVALID`: validate the complete service-account JSON.
- `BIGQUERY_PERMISSION_DENIED`: grant dataset, query-job, and Storage Write API
  permissions to the runtime identity.
- `BIGQUERY_DATASET_NOT_FOUND`: verify project and dataset IDs.
- `BIGQUERY_LOCATION_MISMATCH`: use the dataset's actual location.
- Connector discovery failures: verify
  `com.wepay.kafka.connect.bigquery.BigQuerySinkConnector` appears in
  `/connector-plugins` on every Connect worker.

