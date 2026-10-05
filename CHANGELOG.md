# Changelog

CDC payload sampling through the API has been removed (legacy `/events` returns
410). Topic metadata remains available; verification reads Kafka/storage directly.

All notable changes are recorded here. ClueCDC follows Semantic Versioning.

## [Unreleased]

### Fixed

- Compose startup with metadata at revision `5e2d8a9f1c30`: transactional,
  data-preserving upgrade to `0001` and serialized PostgreSQL startup migrations.

### Added

- AWS S3 and MinIO destinations with encrypted credentials, safe SDK probes,
  Aiven 3.4.2 JSONL delivery, and real MinIO CDC content acceptance.
- A separate worker process with renewable job/notification leases and
  PostgreSQL concurrency coverage.

- Open-source governance, security, support, contribution, and issue templates.
- Centralized configuration validation, provider extension seams, health aliases,
  structured request logging, and repository CI.
- Official Material for MkDocs site, GitHub Pages deployment, and Kubernetes baseline.

### Changed

- Canonical Connection persistence replaces source/destination mirror tables;
  pipelines and deliveries use connection foreign keys.
- Migrations are squashed into a documented fresh-install baseline.
- Collection endpoints batch related metadata queries.
- Filesystem App Router pages replace the catch-all dispatcher; large UI files
  are split by feature and screen.

- Local configuration and container builds are safer and reproducible.
- Repository links and publication metadata target the ClueData organization.
- Next.js and its ESLint configuration are updated to 16.3.8 to include the
  upstream `next/og` remote-code-execution fix.
- External operations release metadata transactions and row locks before
  network calls.
- The pipeline wizard provisions topics before previewing delivery connector
  configuration.
- End-to-end checks follow the current UI, support alternate local ports, and
  clean up delivery resources before their parent pipelines.
- The compact navigation header no longer overflows narrow mobile viewports.

## [0.1.0]

Initial early-development release line. Earlier commit history is not presented
as a fabricated release history.
