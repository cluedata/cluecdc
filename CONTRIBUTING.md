# Contributing to ClueCDC

Thank you for helping improve ClueCDC. By participating, you agree to follow
the [Code of Conduct](CODE_OF_CONDUCT.md). Security reports must follow
[SECURITY.md](SECURITY.md), not a public issue.

## Development setup

The supported toolchain is Docker Compose v2.24+ or v5 with Linux containers,
Python 3.12, and Node.js 24. See the
[platform prerequisites](docs/getting-started/prerequisites.md) for macOS,
Ubuntu/Linux, and Windows/WSL 2.

```bash
git clone <repository-url>
cd cluecdc
cp .env.example .env
docker compose up -d --build --wait
```

The Compose-only setup uses the public, loopback-only demo values from
`.env.example` and does not require host Python or Node.js. Those tools are
needed for host-based development and tests. See
[local development](docs/development/setup.md) and
[optional unique local secrets](docs/operations/configuration.md#optional-unique-local-secrets).

## Architecture

ClueCDC is a modular monolith: Next.js web, FastAPI API/core, PostgreSQL
metadata, and adapters for databases, Kafka, Kafka Connect, and Debezium.
Domain services must not import UI code or call Kafka Connect directly from
route handlers. Start with the [architecture overview](docs/architecture/overview.md).

Database capabilities belong behind `apps/api/app/providers`; infrastructure
calls belong in `adapters`; orchestration belongs in `services`; HTTP contracts
belong in `api` and `schemas`. Do not add edition checks to community logic.

## Quality commands

```bash
npm ci
python -m pip install -e "apps/api[dev]"
npm run format:check
npm run lint
npm run typecheck
npm test
npm run build
```

Run live integration and browser tests only after starting Compose; instructions
and cleanup behavior are in [testing](docs/development/testing.md).

## Database migrations

Create a migration with `alembic revision --autogenerate -m "description"`
from `apps/api`. Apply with `alembic upgrade head`; roll back one revision with
`alembic downgrade -1`. Review generated SQL and both upgrade/downgrade paths.

## Adding a source or destination connector

Follow [adding a connector](docs/development/adding-connector.md). Add provider
metadata, typed configuration construction, adapters, unit tests, integration
tests, docs, and safe secret handling. Unsupported capabilities must remain
explicitly unavailable rather than simulated.

## Adding UI pages

Use the shared shell and components in `packages/ui`; keep business and
Debezium configuration logic in the API. Provide loading, actionable error,
empty, and destructive-confirmation states. Never store database credentials in
browser persistence.

## Pull requests

- Keep changes focused and backward compatible when practical.
- Add or update tests and docs with behavior changes.
- Use imperative commit subjects, for example `Add MySQL readiness checks`.
- Explain operational impact, migrations, screenshots, and breaking changes.
- Confirm logs, fixtures, screenshots, and test output contain no secrets.
- Sign off commits only if required by the repository's configured policy.

Maintainers may ask for a design issue before accepting broad architecture or
new infrastructure work.
