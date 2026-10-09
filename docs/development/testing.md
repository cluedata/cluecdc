# Testing

## GitHub Actions

Automatic CI on pushes to `main`/`master` and pull requests runs application
quality/tests, documentation, dependency/security scans, container builds, core
smoke tests, real PostgreSQL/MySQL/object-storage CDC flows, and browser E2E.
For a manually dispatched CI run, enable `run_full_ci`; when it is false, only
the documentation job runs.

The separate Documentation workflow continues to build and deploy GitHub Pages
on documentation changes pushed to `main`, or when manually triggered.
The Documentation workflow builds and publishes the site only; correctness and
security coverage comes from the CI workflow.

## Fast checks

```bash
npm ci
python -m pip install -e "apps/api[dev]"
npm run format:check
npm run lint
npm run typecheck
npm run test:web
npm run lint:api
npm run typecheck:api
npm run test:api
npm run build
python scripts/check-repository.py
```

API unit tests use temporary SQLite and mocked external clients where isolation is intentional. Web unit tests use Vitest and Testing Library.

`WORKER_TEST_DATABASE_URL` enables a real PostgreSQL concurrency test: two
workers race for one job and only one claim succeeds. Each test creates and
removes its own UUID-named schema. Opt-in full CI provides a dedicated PostgreSQL service.
Lease recovery, backoff, terminal failure, result persistence and cancellation
also have unit coverage. Collection tests assert SQL query counts stay constant
as the number of resources grows.

## Live integration

Start the integration stack with
`docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait`, run
`scripts/demo.py`, `scripts/demo-destination.py`, and
`scripts/demo-object-storage.py`, then run Playwright. These
checks use the real source, Debezium, Kafka, Connect, destination, API proxy,
and browser path. Test artifacts are ignored and must be sanitized before
sharing.

The object-storage check mutates the real PostgreSQL source, waits for Debezium
and the Aiven sink, decompresses generated MinIO objects and checks c/u/d event
content, record keys, before/after values, timestamps and source metadata. It
needs no AWS account. Its sanitized report is
`artifacts/object-storage-verification.json`.

Documentation is validated with `mkdocs build --strict`.
