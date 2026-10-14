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
`scripts/cdc-integrity.py`, `scripts/demo-object-storage.py`, and then
Playwright. The older focused demo scripts remain useful for manual UI smoke
testing. These
checks use the real source, Debezium, Kafka, Connect, destination, API proxy,
and browser path. Test artifacts are ignored and must be sanitized before
sharing.

For an isolated stack that does not depend on a repository `.env` file:

```bash
docker compose --env-file .env.example --env-file .env.test.example \
  -p cluecdc-acceptance -f compose.yaml -f compose.test.yaml \
  up -d --build --wait --wait-timeout 300
python scripts/cdc-integrity.py \
  --api-url http://localhost:3000/api/v1 \
  --compose-project cluecdc-acceptance \
  --env-file .env.example --env-file .env.test.example
python scripts/cdc-recovery.py \
  --api-url http://localhost:3000/api/v1 \
  --compose-project cluecdc-acceptance \
  --env-file .env.example --env-file .env.test.example
python scripts/demo-object-storage.py \
  --api-url http://localhost:3000/api/v1 \
  --compose-project cluecdc-acceptance \
  --env-file .env.example --env-file .env.test.example
docker compose --env-file .env.example --env-file .env.test.example \
  -p cluecdc-acceptance -f compose.yaml -f compose.test.yaml down
```

The integrity matrix creates run-scoped tables and tests PostgreSQL and MySQL
sources against both JDBC destinations. It reconciles the initial snapshot and
streamed materialized state by primary key after transactions, inserts,
sequential updates, deletes and a primary-key update. Values include NULL,
UTF-8, high-precision decimals and timestamps. Decimal scale and equivalent UTC
offsets are normalized during comparison, and mismatch errors report bounded
keys/field names without dumping row contents.

JDBC delivery is at least once. The default upsert plus stable record keys makes
replay idempotent at the materialized-row level, but ClueCDC does not claim
distributed exactly-once delivery. Insert mode is not replay-idempotent. MySQL
`DATETIME` has no stored zone; ClueCDC treats the integration fixture as UTC.
PostgreSQL `timestamptz` values are compared as UTC instants. Destination
constraints, engine-specific types, triggers and generated values can still
produce semantics that require application-specific reconciliation.

The object-storage check mutates the real PostgreSQL source, waits for Debezium
and the Aiven sink, decompresses generated MinIO objects and checks c/u/d event
content, record keys, before/after values, timestamps and source metadata. It
needs no AWS account. Its sanitized report is
`artifacts/object-storage-verification.json`.

Object storage is checked as an append-only CDC event log, not as a materialized
table. The test validates keyed create/update/delete envelopes and before/after
content. At-least-once retries may create duplicate events, so downstream
consumers must deduplicate by topic, partition and offset.

Documentation is validated with `mkdocs build --strict`.
