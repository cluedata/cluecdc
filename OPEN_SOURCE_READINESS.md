# Open-source readiness report

Prepared on 2026-10-01 for `cluedata/cluecdc`.

## Audited architecture

ClueCDC is a TypeScript/Python control plane. Next.js calls a FastAPI API
through a same-origin proxy. PostgreSQL stores application metadata. The API
manages Kafka and Kafka Connect but never transports CDC row records.

The implemented data plane is:

```text
PostgreSQL or MySQL source
  -> Debezium source connector in Kafka Connect
  -> Apache Kafka
  -> JDBC sink connector in Kafka Connect
  -> PostgreSQL or MySQL destination
```

PostgreSQL and MySQL have working source and JDBC destination adapters. AWS S3
and MinIO have destination-only connection tests and Aiven S3 sink configuration.
Only providers backed by implementation and automated coverage are exposed.

## Deployment boundary

The default `compose.yaml` starts six core services: metadata PostgreSQL,
Kafka, Kafka Connect, API, background worker, and web. Optional database
inspection is in `compose.dev.yaml`. PostgreSQL/MySQL source and destination
fixtures plus MinIO are in `compose.test.yaml`.

The Kubernetes baseline deploys ClueCDC metadata, API, web, and distributed
Kafka Connect workers. Kafka is deliberately external. Production still
requires ingress, TLS, authentication, external secrets, backups, monitoring,
published images, and a replicated Kafka deployment.

## Verification policy

CI separates source quality/tests, documentation, security, container builds,
and live smoke/E2E checks. Integration jobs compose the core runtime with the
test override; fixture databases are not part of the default smoke topology.

Release checks include:

- Ruff, mypy, pytest, ESLint, TypeScript, Prettier, Vitest, and Next.js build;
- strict MkDocs build and internal-link validation;
- Compose validation for core, development, and test combinations;
- Kustomize rendering and Alembic upgrade/drift checks;
- dependency and secret scans; and
- real CDC smoke and Playwright flows when Docker is available.

Test results in this document must be refreshed from the current working tree
before publication; historical counts are intentionally not presented as
current evidence.

## Remaining publication actions

Maintainers must confirm copyright and contribution permissions, dependency
notices, branding rights, and the Apache-2.0 licensing decision. They must also
review the diff, configure branch protection and GitHub Pages, publish
versioned container images, and perform a manual documentation/UI pass.

No automated workflow or cleanup task in this repository force-pushes or
publishes a release.
