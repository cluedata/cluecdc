---
title: Current feature matrix
description: Implemented ClueCDC capabilities, access levels, and product boundaries.
---

# Current feature matrix

This page describes behavior implemented by the current source tree. A checked
item is available now; planned work is listed separately and is not presented as
implemented functionality.

## Capture and source management

- [x] Reusable PostgreSQL 13+ and MySQL 8 source connections.
- [x] Unsaved and saved connection tests with write-only credentials.
- [x] Asynchronous source discovery and health jobs with persisted results.
- [x] Namespace, table, column, primary-key, index, and schema inspection.
- [x] PostgreSQL logical-replication and MySQL binlog/readiness checks.
- [x] Explicit table selection and exact Debezium table filters.
- [x] Initial, streaming-only (`no_data`), never, and always snapshot modes.
- [x] Debezium capture configuration preview before pipeline metadata is saved.

## Pipelines and snapshots

- [x] Pipeline creation across a selected source, Kafka cluster, and Connect cluster.
- [x] Capture validation, topic preparation, and Debezium connector deployment.
- [x] Pause, resume, connector restart, task restart, and explicit deletion.
- [x] Desired state stored separately from observed connector and task state.
- [x] Add or remove a captured table through durable background operations.
- [x] Incremental resnapshot and active-snapshot stop through Debezium signals.
- [x] Operation progress, failure details, and safe retry from persisted checkpoints.
- [x] Initial and incremental snapshot status when Debezium notifications are available.

The web creation wizard performs capture preview, saves the pipeline, deploys
capture, prepares topics, validates delivery, and deploys the first delivery.
The API keeps these as explicit endpoints so automation can control each stage.

## Destinations and deliveries

- [x] Reusable PostgreSQL and MySQL database destinations.
- [x] PostgreSQL/MySQL JDBC sink delivery with explicit topic-to-table mapping.
- [x] Insert or upsert mode, delete propagation, automatic table creation, and
  optional basic schema evolution.
- [x] Reusable AWS S3 and MinIO object-storage destinations.
- [x] Aiven S3 sink delivery using gzip or plain JSONL Debezium envelopes.
- [x] Multiple independent deliveries per capture pipeline.
- [x] Preview checks for topics, source schema/keys, target access, plugins, and
  Kafka Connect configuration before deployment.
- [x] Delivery pause, resume, connector restart, task restart, mapping update,
  status observation, and removal without deleting destination data.

## Kafka and Kafka Connect

- [x] Register and manage Kafka and Kafka Connect clusters.
- [x] Inspect Kafka topics, partitions, beginning/end offsets, and consumer groups.
- [x] Delete a Kafka topic through a separately privileged destructive action.
- [x] Discover Connect plugins and inspect connectors and task states.
- [x] Resolve encrypted connector credentials at runtime through the private
  machine-authenticated ConfigProvider endpoint.

ClueCDC does not expose CDC record payloads. The legacy `/api/v1/events`
endpoint returns HTTP 410; inspect records directly with Kafka tooling or in
the configured destination.

## Operations and observability

- [x] Operational overview of source, capture, delivery, and incident state.
- [x] Connector/task reconciliation and normalized Error Center incidents.
- [x] Error acknowledge/resolve lifecycle and sanitized failure details.
- [x] Alert firing, acknowledgement, silence, unsilence, and recovery lifecycle.
- [x] Filtered alert rules with cooldown and optional recovery notifications.
- [x] Slack Incoming Webhook, Telegram, and SSRF-hardened generic webhook channels.
- [x] Immutable audit records for metadata changes, lifecycle actions, jobs,
  authentication, authorization-sensitive administration, and user management.
- [x] JSON logs, correlation IDs, live/ready health endpoints, and Prometheus
  control-plane/alert metrics.

Historical throughput, lag, freshness, and no-event measurements remain
unavailable unless a stream metrics provider supplies them. The UI reports
unknown values instead of inventing zeroes.

## Authentication, users, and authorization

- [x] Local email/password authentication with Argon2id hashes.
- [x] Minimum password length of 8 characters for invited users and CLI-created Admins.
- [x] Opaque `HttpOnly`, `SameSite=Lax` server-side sessions with expiry and logout.
- [x] Single-use expiring invite links; raw invite/session tokens are never stored.
- [x] Admin user invitation, role change, enable, disable, and permanent deletion.
- [x] Self-disable/self-delete and last-active-Admin safeguards.
- [x] Public development-only Admin account for a fresh loopback Compose stack;
  production rejects this bootstrap mode.
- [x] API-enforced Viewer, Ops, and Admin permissions with unauthorized UI
  navigation and actions hidden.

| Workspace/capability | Viewer | Ops | Admin |
| --- | :---: | :---: | :---: |
| Overview, Pipelines, and Deliveries | View | View/operate | Full |
| Sources and Destinations | Hidden | View only | Full |
| Create/update/delete Pipelines and Deliveries | No | Yes | Yes |
| Kafka/Connect inventory in main navigation | Hidden | Hidden | Full |
| Monitoring, alerts, errors, audit, and settings | Hidden | Hidden | Full |
| Users and roles | Hidden | Hidden | Full |

Ops receives read-only Kafka/Connect API access needed by the pipeline wizard,
but the standalone infrastructure workspace and all infrastructure mutations
remain Admin-only. See [Roles and permissions](security/roles.md) for the
authorization contract.

## Packaging and deployment

- [x] FastAPI/OpenAPI API, Next.js web UI, and a separate reconciliation/job worker.
- [x] PostgreSQL metadata with explicit Alembic migrations and drift checking.
- [x] Six-service loopback Docker Compose runtime with optional development and
  integration-test overlays.
- [x] Kubernetes Kustomize baseline for API, web, worker, metadata PostgreSQL,
  and a two-worker distributed Connect cluster using external Kafka.
- [x] Automated unit, type, lint, build, migration, container, live CDC, object
  storage, and browser checks in the repository workflows/release gate.

## Current boundaries

- [ ] SSO, OAuth/OIDC, LDAP, SAML, groups, and custom roles are not implemented.
- [ ] ClueCDC does not send invitation email; an Admin shares the generated link.
- [ ] CDC payload browsing through the ClueCDC API/UI is intentionally unavailable.
- [ ] Automatic TLS, Kafka authentication/ACL provisioning, and production
  external-secret integration remain deployment responsibilities.
- [ ] The bundled Compose environment is for local use, not high availability.
