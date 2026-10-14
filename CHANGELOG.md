# Changelog

All notable changes to ClueCDC are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Added centralized production configuration validation for HTTPS/CORS origins,
  authenticated PostgreSQL metadata, unique high-entropy service secrets, and
  known development credential rejection without exposing configured values.
- Enforced production origin checks for cookie-authenticated writes, hardened
  cookie expiry and browser response headers, and added a TLS-terminating
  Kubernetes Ingress baseline without trusting client forwarding headers.
- Expanded central response/audit redaction for transport credentials and
  private keys, verified in-place encrypted credential rotation, and removed
  unnecessary Kubernetes service-account token mounts from every workload.
- Made login throttling metadata-backed across API replicas, invalidated
  sessions after role changes, protected the last active Admin from demotion,
  and added a regression test requiring server-side auth on every private API.
- Added SSL and SASL_SSL/SASL_PLAINTEXT Kafka clients with PLAIN and SCRAM,
  encrypted TLS/SASL material, verified certificates, ConfigProvider-only
  connector overrides, and independently configured secure Connect workers.

### Added

- Added an isolated live CDC integrity matrix covering PostgreSQL and MySQL
  sources against both JDBC destinations, with snapshot/stream reconciliation,
  transactions, CRUD, primary-key updates, NULL, UTF-8, decimal and timestamp
  cases, plus event-semantic MinIO JSONL verification.
- Added a restart/offset-recovery acceptance test across connector tasks,
  Kafka Connect, API, worker, Kafka and PostgreSQL outages; missing WAL, binlog,
  schema-history and Kafka offset failures now return redacted recovery hints.
- Added live snapshot and schema-safety coverage for table add/remove,
  resnapshot repair, interruption and cancellation; compatible destination
  columns are provisioned additively while breaking and unsupported changes
  remain explicit operator actions.
- Added non-mutating PostgreSQL logical-slot and retained-WAL diagnostics,
  MySQL binlog retention/position inventory, configurable outage-window checks,
  permission-limited warnings, and critical retention-risk alert recovery.
- Hardened reconciliation so dependency outages preserve pause/stop intent and
  one resource failure cannot cancel the worker batch; added live fault
  injection for Connect, API/worker, source, and destination recovery without
  connector recreation or implicit offset reset.
- Added durable worker failure classification, bounded configurable exponential
  backoff with jitter, attempt history and manual terminal-job retry; permanent
  authentication, configuration, schema, and missing-history errors no longer
  enter automatic retry loops.
- Serialized connector lifecycle mutations with expiring durable leases, made
  capture and delivery deploy retries adopt only exact deterministic connector
  configurations, and suppressed duplicate table-operation execution without
  holding database locks across external calls.
- Added PostgreSQL metadata backup, archive/checksum validation, guarded
  isolated restore, Alembic compatibility checks, and encrypted-secret
  verification with a separately preserved key fingerprint.
- Added a guarded v0.2.0 metadata upgrade preflight and runner that requires a
  verified backup and compares entity identities, counts, ciphertext, secret
  decryption, Alembic head, and schema drift before promotion.

## [0.2.0] - 2026-10-09

### Added

- Added local email/password authentication with Argon2id, server-side HttpOnly
  sessions, expiring single-use invite links, and first-Admin CLI bootstrap.
- Added built-in Admin, Ops, and Viewer authorization, user management screens,
  a current-user menu, authentication audit events, and security documentation.
- Added an explicitly development-only public Admin account for local demos;
  production startup rejects the default-account bootstrap.
- Added Admin-only user deletion with self-deletion and last-Admin safeguards.

### Changed

- Made session authentication the production mode and restricted destructive
  infrastructure and identity operations to Admins.
- Narrowed Viewer access to Overview, Pipelines, and Deliveries; made Sources
  and Destinations read-only for Ops while allowing full Pipeline and Delivery
  lifecycle operations; unauthorized navigation and controls are hidden.
- Reduced the invite password minimum to eight characters.

### Fixed

- Mapped compatibility developer-mode authorization to a persistent active
  Admin so user invitations always have a valid creator.
- Normalized the unique email and authentication-token indexes so Alembic drift
  checks agree with PostgreSQL metadata after upgrading.

## [0.1.0]

### Added

- A self-hosted control plane for PostgreSQL and MySQL CDC sources using
  Debezium, Apache Kafka, and Kafka Connect.
- PostgreSQL and MySQL JDBC destinations with independent delivery lifecycle,
  mappings, connection validation, and delete propagation.
- AWS S3 and MinIO destinations using the Aiven S3 sink, encrypted credentials,
  endpoint-aware validation, and gzip JSONL CDC envelope delivery.
- Pipeline, connector, topic, schema, alert, audit, and health management through
  the FastAPI API and Next.js UI.
- Docker Compose development topology, optional integration fixtures, Kubernetes
  deployment baseline, MkDocs documentation, and focused unit/E2E coverage.
- Tag-driven release automation for immutable GHCR images, Python artifacts,
  checksums, and GitHub Releases.

### Changed

- Consolidated reusable sources and destinations into a canonical connection
  model while retaining compatible API aliases.
- Split background reconciliation and notification work into a separately
  health-checked worker with renewable leases and bounded concurrency.
- Reduced collection query counts and moved external operations outside database
  transactions and row locks.
- Replaced catch-all frontend dispatch with filesystem routes and feature-scoped
  components.
- Limited the default Compose stack to six runtime services; database, MinIO,
  workload, and inspection fixtures live in explicit overlays.

### Fixed

- Serialized PostgreSQL startup migrations and added a transactional,
  data-preserving bridge from prototype revision `5e2d8a9f1c30`.
- Provisioned Kafka topics before delivery preview and improved connector
  lifecycle compensation, validation errors, empty states, and mobile navigation.
- Updated Next.js to include the upstream `next/og` remote-code-execution fix and
  updated the lockfile to remove vulnerable runtime `source-map-js` and `sharp`
  versions.
- Restored application, security, container, migration, and live CDC checks on
  pull requests and `main` instead of limiting them to manual CI runs.

### Security

- Encrypted stored connection and notification credentials and passed Kafka
  Connect only secret references resolved through an authenticated internal
  endpoint.
- Redacted secrets from validation errors, connector traces, logs, API responses,
  and audit records; blocked unsafe notification webhook destinations.
- Required token authentication and non-example secrets in production, bound
  local ports to loopback, and added repository, dependency, and secret scans.

[Unreleased]: https://github.com/cluedata/cluecdc/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/cluedata/cluecdc/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cluedata/cluecdc/releases/tag/v0.1.0
