# Open-source readiness report

Prepared on 2026-10-01 for the proposed public repository
[`cluedata/cluecdc`](https://github.com/cluedata/cluecdc).

This report describes the isolated release candidate in
`D:\cluecdc-open-source`. It does not claim that the project has been
published, that ClueData owns every contribution, or that the proposed license
has received legal approval.

## 1. Existing architecture and capability audit

ClueCDC is a TypeScript/Python control plane for change data capture. The web
application runs on Next.js 16 and React 19. It calls an asynchronous FastAPI
API through a same-origin proxy. The API stores control-plane state in
PostgreSQL through SQLAlchemy and Alembic. Debezium, Apache Kafka, and Kafka
Connect form the data plane.

The audited implementation currently provides:

- PostgreSQL and MySQL connection discovery, readiness checks, and Debezium
  source connector management;
- PostgreSQL and MySQL database delivery connector management;
- S3-compatible object storage, including MinIO, and an Iceberg Hadoop catalog
  delivery path;
- pipeline lifecycle controls, topic provisioning, snapshots, table
  publication, connector health, alerts, audit records, structured API logs,
  and Prometheus-format metrics; and
- a development bearer-token authentication mode.

Important boundaries are explicit:

- SQL Server and Trino are documented as planned rather than implemented.
- Historical metrics and lag charts require an external metrics provider; the
  API reports unavailable data instead of inventing it.
- Production SSO, fine-grained RBAC, and an external secret manager are not
  included.
- Docker Compose is a local-development and verification environment, not a
  production topology.
- The Kubernetes directory is a Kustomize baseline. It expects externally
  managed Kafka, production secrets, storage, ingress, TLS, and published
  application images.

## 2. Isolation and source-history decision

Work was performed in `D:\cluecdc-open-source`. The original checkout at
`D:\cluecdc` was used only as a source and was not edited or deleted.
Credentials, runtime volumes, dependency directories, caches, test output, and
build output were excluded from the copy.

The release candidate is a fresh `main` repository. The original repository's
eight commits were not copied because their author identities and contribution
permissions have not been reviewed. No synthetic history was created. Before
publication, maintainers must decide whether the original history can be
published or whether the reviewed snapshot should be the public initial
commit.

## 3. Refactoring and release-preparation changes

The isolated tree now includes:

- Apache-2.0 license and notice files, contribution and governance policies,
  support and security policies, release guidance, issue forms, and a pull
  request template;
- reproducible Node/Python installation metadata, centralized sample
  configuration, repository validation, pre-commit configuration, and safer
  container build contexts;
- CI jobs for source quality, tests, production builds, documentation,
  dependency and secret scanning, container builds, database migration drift,
  live smoke tests, and browser end-to-end tests;
- a strict Material for MkDocs documentation site and a GitHub Pages workflow;
- local Docker deployment guidance and a namespaced Kubernetes/Kustomize
  baseline with probes, resources, multiple web/Connect replicas, and an API
  migration init container;
- an Alembic migration aligning the nullable lakehouse catalog relationship
  with its `SET NULL` model behavior;
- topic preparation before database and lakehouse delivery previews;
- consistent destination-before-delivery locking to prevent PostgreSQL
  deadlocks during concurrent lifecycle operations and status polling;
- responsive-header, end-to-end cleanup, alternate-port, and connector-status
  verification fixes; and
- an explicit unknown-version fallback when package metadata is unavailable.

## 4. Repository structure

```text
apps/api/                 FastAPI control plane, migrations, and Python tests
apps/web/                 Next.js UI, unit tests, and Playwright tests
deploy/docker/            Docker deployment notes
deploy/kubernetes/        Kustomize baseline
docs/                     Official documentation source
examples/                 End-to-end usage examples
infrastructure/           Container images and local service initialization
packages/                 Shared TypeScript packages
scripts/                  Bootstrap, smoke, workload, and repository checks
.github/workflows/        CI and GitHub Pages automation
docker-compose.yml        Local integration environment
mkdocs.yml                Documentation navigation and theme configuration
```

## 5. Security, secrets, and license review

No local `.env` or known runtime credential was copied. The isolated `.env` is
ignored and was generated with local-only random values. Gitleaks reports no
finding in the intended staged tree. A scan of the original eight-commit
history found only repeated instances of the documented dummy Fernet sample;
the narrow `.gitleaks.toml` allowlist matches that exact sample rather than a
broad path or rule.

At the time of this report, `npm audit --audit-level=high` and `pip-audit`
reported no known vulnerable installed dependency. These scans reduce risk but
do not establish that the software is vulnerability-free.

Apache License 2.0 is the proposed project license. Direct Python dependencies
reviewed were generally permissive; the published metadata for
`confluent-kafka` needs a maintainer/legal confirmation. The JavaScript
dependency graph contains transitive artifacts with LGPL, MPL-2.0, and CC-BY
notices, including native image-processing distribution artifacts. Before a
public release, counsel or an authorized maintainer must verify copyright and
contribution ownership, the original commit authors, dependency redistribution
obligations, required notices, branding rights, and approval of Apache-2.0.

## 6. Verification results

The release candidate passed the following checks on Windows with Node 24,
Python 3.12-compatible tooling, Docker Desktop, and Chromium:

- repository policy and configuration validator;
- Prettier, ESLint, TypeScript, and Next.js production build;
- 44 Vitest tests across 15 files;
- Ruff lint/format, mypy across 69 source files, and 120 pytest tests;
- strict MkDocs build, Docker Compose validation, Kubernetes rendering, and
  YAML parsing;
- editable Python package installation, dependency audits, and staged secret
  scan;
- API and web container builds plus a live Alembic drift check;
- source CDC smoke flow and two-destination fan-out, including insert, update,
  pause/catch-up, resume, restart, and cleanup; and
- 8 Playwright scenarios against the real container stack, covering source and
  destination wizards, real row propagation, lifecycle controls, incidents,
  recovery, navigation, event exploration, and desktop/mobile layouts.

The final observed automated totals were 44 web unit tests, 120 API tests, and
8 browser end-to-end scenarios, all passing.

## 7. Documentation implementation

The official site uses Material for MkDocs, strict builds, built-in search,
copyable code blocks, light/dark palettes, ClueCDC orange branding, Mermaid
diagrams, canonical URLs, repository links, and edit links. Its information
architecture covers getting started, concepts, connections, connectors,
pipelines, lakehouse delivery, operations, deployment, troubleshooting, API,
and development.

Local preview:

```powershell
cd D:\cluecdc-open-source
python -m pip install -r requirements-docs.txt
mkdocs serve
```

Then open `http://127.0.0.1:8000`. The strict static build is automated, but a
final human visual review of the generated site is still recommended on
desktop and mobile browsers.

## 8. GitHub Actions configuration

`.github/workflows/ci.yml` defines four independently visible jobs:

- `quality`: repository checks, rendered deployment configuration, source
  formatting/linting/type checks, unit tests, and production builds;
- `documentation`: strict documentation build;
- `security`: npm and Python dependency audits plus Gitleaks; and
- `containers-and-smoke`: image builds, complete Compose startup, Alembic
  drift detection, live source and destination smoke flows, Playwright, secret
  response/storage checks, artifact upload, and cleanup.

`.github/workflows/docs.yml` builds and deploys the site to GitHub Pages using
the official configure-pages, upload-pages-artifact, and deploy-pages actions.
It grants only the permissions needed for Pages deployment and uses the
`github-pages` environment.

## 9. Remaining release blockers and limitations

Publication is intentionally not complete. The following items require an
authorized maintainer:

1. Confirm copyright, contributor permissions, dependency notices, trademark
   usage, and the Apache-2.0 licensing decision.
2. Decide whether to publish the reviewed snapshot or a legally reviewed form
   of the original history.
3. Review the staged initial commit, configure Git author identity, and push it
   without force only after confirming the target repository is still empty.
4. Configure GitHub repository, Pages, security, and branch-protection settings
   listed below.
5. Publish signed/versioned API, web, and Connect images to GHCR before using
   the Kubernetes baseline as written.
6. Perform a manual visual pass of the documentation site. Automated strict
   rendering passed, but the in-app browser automation interface required for
   that visual review was unavailable in this workspace.
7. Add production SSO/RBAC, external secret management, TLS/SASL, backups,
   observability retention, and a managed Kafka plan before a production
   deployment.
8. Expand live integration coverage for MySQL capture and Iceberg delivery.
   Their provider/unit coverage and local services passed, but this release
   review's complete row-level browser flow focused on PostgreSQL.

The target GitHub repository appeared empty when reviewed on 2026-10-01, but
that state must be checked again immediately before pushing.

## 10. Publication commands and manual GitHub settings

Run these commands only after the legal and ownership checks above are closed.
They deliberately contain no force push:

```powershell
cd D:\cluecdc-open-source
git status
git diff --cached --stat
python scripts/check-repository.py

git config user.name "AUTHORIZED MAINTAINER NAME"
git config user.email "AUTHORIZED MAINTAINER EMAIL"
git commit -m "Initial open-source release"

git remote add origin https://github.com/cluedata/cluecdc.git
git fetch origin
git ls-remote --heads origin
```

If `git ls-remote --heads origin` prints any branch, stop and reconcile the
remote history; do not force push. If it is still empty, publish with:

```powershell
git push -u origin main
```

After the first push, an organization or repository administrator should:

- set the repository description, homepage
  `https://cluedata.github.io/cluecdc/`, topics, Issues, and (if desired)
  Discussions;
- set Pages **Source** to **GitHub Actions** and review deployment protection
  for the `github-pages` environment;
- protect `main`, require pull requests and conversation resolution, block
  force pushes/deletion, and require the `quality`, `documentation`,
  `security`, and `containers-and-smoke` checks;
- enable private vulnerability reporting, the dependency graph, Dependabot
  alerts/security updates, and any organization-required code scanning;
- verify Actions policy permits the referenced official actions and grants the
  Pages workflow its declared token permissions;
- configure GHCR package visibility and least-privilege image publication,
  then replace placeholder image tags with immutable release tags or digests;
  and
- create the first signed release/tag only after the release checklist in
  `RELEASES.md` is satisfied.
