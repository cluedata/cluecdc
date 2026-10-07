# Changelog

All notable changes to ClueCDC are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/cluedata/cluecdc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cluedata/cluecdc/releases/tag/v0.1.0
