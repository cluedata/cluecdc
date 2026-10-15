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

The separate **Hardening acceptance** workflow is manually dispatched for
expensive fault injection and is also called by the Release workflow. Both
container publication and GitHub Release creation depend on it; a failed live
gate cannot be bypassed by a successful unit/build job. The workflow also runs
a separate isolated kind/Calico/NGINX job for real production rollout, HTTPS
session boundaries and NetworkPolicy allow/deny/revocation. Preparation never
dispatches a workflow, pushes a tag, or publishes an image automatically.

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
Lease recovery, classified transient/permanent failures, capped exponential
backoff with jitter, terminal failure, manual retry with retained history,
result persistence and cancellation also have unit coverage. Collection tests
assert SQL query counts stay constant as the number of resources grows.
Concurrency coverage verifies deterministic deploy adoption, exact-config
collision rejection, connector lifecycle operation leases, stale-owner
recovery, duplicate table-operation suppression, and reuse of a pending
automatic job by manual retry. The PostgreSQL acceptance check races two
independent metadata sessions and requires exactly one lifecycle lease winner.
The v0.2.0 upgrade acceptance test restores populated metadata, downgrades only
the isolated fixture to released revision `0003`, adds non-empty session/invite
fixtures, takes a verified pre-upgrade backup, and runs `metadata_upgrade.py` to
head twice. Counts, identity hashes, ciphertext hashes, secret decryption, drift
checking, and idempotency must all pass before the fixture database is removed.

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
python scripts/cdc-schema-safety.py \
  --api-url http://localhost:3000/api/v1 \
  --compose-project cluecdc-acceptance \
  --env-file .env.example --env-file .env.test.example
python scripts/cdc-retention-safety.py \
  --api-url http://localhost:3000/api/v1 \
  --compose-project cluecdc-acceptance \
  --env-file .env.example --env-file .env.test.example
python scripts/cdc-failure-recovery.py \
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

The schema-safety acceptance test verifies initial and incremental snapshots,
add/remove table isolation, restart during a 5,000-row snapshot, cancellation
and concurrent-operation rejection. It also validates additive nullable and
defaulted columns, widening/breaking schema classifications, explicit
resnapshot backfill, and actionable rejection of unsupported destination types.

The retention-safety acceptance test verifies PostgreSQL logical-slot inventory,
retained-WAL measurement and non-mutating inactive-slot warnings. It also checks
MySQL's exact configured expiration, current position/file inventory, comparison
with the configured outage objective, and the critical alert recovery lifecycle.

The failure-recovery test requires the integrity and recovery artifacts from the
preceding commands. It injects Connect REST, API/worker, source database, and
destination database failures. The test asserts that manual pause intent
survives unavailable observed state and restarts, dependency failures are
visible, delivery catches up after recovery, and no connector is recreated or
silently reconfigured. A rerun safely resumes its run-scoped fixture if an
earlier interrupted test left it paused.

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

## Combined hardening gate (TEST-01)

```bash
python scripts/hardening_acceptance.py
```

The runner creates its own UUID-scoped Compose project and selects unused
loopback ports. It refuses existing project resources, waits for health with
explicit deadlines, runs integrity → connector recovery → schema/snapshot →
retention → failure injection → object storage → secret boundaries → populated
backup/restore/upgrade → production runtime → authenticated Kafka acceptance,
and removes only resources and volumes owned by that invocation. Existing
Compose projects and their volumes are preserved. Do not direct individual
failure-injection scripts at shared or external infrastructure.

`artifacts/hardening-gate.json` records the candidate SHA, timestamps, stage
results, durations and cleanup outcome. Docker/daemon unavailability produces
`INFRASTRUCTURE_UNAVAILABLE` and exit code 2; executed assertion, build, startup
or data mismatch failures produce `FAIL` and exit code 1. Neither is success.
Bounded sanitized service logs aid diagnosis; raw metadata archives and
temporary TLS private keys are excluded from CI artifact upload.

The CI invocation also supplies `--env-file .env --browser` after creating a
disposable `.env` from the examples and bootstrapping secrets. This runs the
existing demo fixtures and Playwright suite inside the owned project. It does
not overwrite a developer's `.env`; use a disposable checkout for that workflow.

| Requirement/category | Primary verification | Execution |
| --- | --- | --- |
| Unit, API security, RBAC | API pytest and web Vitest | PR CI and release validation |
| SEC-03 TLS/SASL, authenticated source/sink | `kafka_security_acceptance.py` | Live hardening/release gate |
| SEC-04 secret boundaries | `verify-secrets.py` | Smoke and live hardening gate |
| CDC-01 integrity/object storage | `cdc-integrity.py`, `demo-object-storage.py` | Live hardening gate |
| CDC-02 connector restart/offset recovery | `cdc-recovery.py` | Live hardening gate |
| CDC-03 schema/snapshot safety | `cdc-schema-safety.py` | Live hardening gate |
| CDC-04 retention diagnostics | `cdc-retention-safety.py` | Live hardening gate |
| REL-01 failure injection | `cdc-failure-recovery.py` | Live hardening gate |
| REL-02/03 retry, job/operation ownership | pytest, PostgreSQL lease races | PR CI and release validation |
| OPS-01/02 migration/backup/restore | `metadata_acceptance.py`, Alembic checks | Live hardening gate |
| OPS-03 manifests and container lifecycle | `deployment_validation.py`, kubeconform, `deployment_runtime.py`, `kubernetes_acceptance.py` | PR/release and live/Kubernetes gates |
| Browser integration | existing Playwright suite | Smoke CI and release hardening gate |

`metadata_acceptance.py` copies populated live fixture metadata into a new
database, downgrades only that copy to v0.2.0 revision `0003`, ensures sessions
and invites are nonempty, takes a verified backup, runs the guarded upgrade
twice, restores into another empty database, verifies decryption, and rejects
wrong keys/nonempty restore targets. Only exact databases created by the run
are removed. The original database is never downgraded or reset.

Kubernetes validation renders the exact base or `--kustomize-dir` overlay,
checks resource/probe/security/Secret/network boundaries, and validates all
resources against Kubernetes schemas. The runtime harness additionally checks
production settings and read-only containers with random secrets and a separate
PostgreSQL instance. Cluster rollout, ingress TLS and CNI policy enforcement
still require target-cluster evidence before a pilot is approved. The separate
Kubernetes acceptance job supplies repeatable local kind/Calico/NGINX evidence
for those behaviors, including a trusted TLS login, Secure cookies and origin
rejection. Its short-lived certificate fixture also passes a strict verified
TLS handshake unit test; no trust/hostname verification is disabled.
