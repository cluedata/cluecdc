# Testing

## GitHub Actions

Pull requests and pushes to `main`/`master` select independent jobs from changed
paths using `scripts/ci_scope.py`. A lightweight selector reads the complete Git
diff, checks repository safety and publishes the selected scopes. **CI checks**
remains the stable required check and only requires selected jobs to pass;
unrelated jobs are skipped before dependency installation or services start.

| Changed paths | Automatic CI |
| --- | --- |
| `apps/api/**` | Backend/API tests with disposable PostgreSQL worker coverage |
| `apps/web/**`, `packages/ui/**`, root npm manifests | Web format, lint, types, unit tests and production build |
| `packages/contracts/**` | API and web, since both sides use the shared contract |
| `docs/**`, Markdown, MkDocs configuration/dependencies | Strict documentation build only; no publication |
| `deploy/kubernetes/**`, deployment harness/tests | Static deployment validation and deployment contract tests; no live cluster |
| Compose/environment files, `infrastructure/tests/**` | Render Compose configurations; no stack startup |
| CI/release scripts and their contract tests | Targeted tooling tests |
| `.github/workflows/**`, `.github/actions/**` | actionlint and targeted workflow/tooling contracts |
| `scripts/commerce-workload/**` | Workload tests with a separate PostgreSQL fixture |
| Individual Dockerfiles / `infrastructure/connect/**` | Build only the affected image; no push or runtime stack |
| `.dockerignore` | Build API, web and Connect images, whose shared context rules changed |
| License/ignore files | Repository safety only |

Multiple changed folders select the union of their scopes. A move or rename
checks both old and new paths. Unknown paths fail selection with a request to
add an explicit rule; an unavailable comparison also fails selection. Neither
case silently starts every job. On a repository's first push, all tracked paths
are genuinely new and are classified normally. An ordinary PR/push never has an
implicit full-CI fallback.

Manual **CI ? Run workflow** defaults to `scope=changes`, comparing the selected
commit with its first parent, including merge commits. An operator may instead
choose one component explicitly. Release candidates and release tags share one
dedicated validation job with metadata/acceptance checks, direct API tests and
disposable PostgreSQL. They do not invoke the general CI selector or activate
unrelated scopes. Publication requires all configured acceptance gates to PASS.

API tests cover endpoint behavior, validation, authentication, RBAC and backend
service/worker contracts. Real Data Quality providers remain opt-in. Browser,
live CDC and Kubernetes acceptance are still manual. API success does not certify
UI, real-provider data flow or cluster behavior.

Extra validation is available through **Actions / Run workflow**, with no automatic
PR/push or scheduled triggers:

| Manual workflow | Scope |
| --- | --- |
| Extended validation (manual) | Previous web/lint/audit/Compose/image/smoke/CDC/browser checks; `run_full_ci=true` selects all |
| Data Quality | Real PostgreSQL/MySQL rules, migration and browser checks |
| Hardening acceptance | Recovery, Kafka security, metadata restore, browser and Kubernetes fixtures |
| CDC reliability | CRUD, replay and optional failure injection/source scenarios |
| Experimental source CDC acceptance | Optional MariaDB, SQL Server and MongoDB fixtures |
| Documentation | Build and publish GitHub Pages on request |

These workflows are not automatically executed by the release publisher. Their
scripts and local commands remain available. Configured mandatory acceptance
outcomes are checked by `release.py plan --require-final` before publication;
skipping a manual run does not turn a missing gate into PASS. Workflow dispatches
and publishing still require an explicit operator action; changing CI does not run them.

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
removes its own UUID-named schema. Every selected API job provides a dedicated PostgreSQL service.
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
results, durations and cleanup outcome. It updates at stage boundaries, including
timeout failures, so an interrupted run cannot leave a previous PASS report.
Docker/daemon unavailability produces
`INFRASTRUCTURE_UNAVAILABLE` and exit code 2; executed assertion, build, startup
or data mismatch failures produce `FAIL` and exit code 1. Neither is success.
Bounded sanitized service logs aid diagnosis; raw metadata archives and
temporary TLS private keys are excluded from CI artifact upload.

The manual hardening workflow supplies `--env-file .env --browser` after creating a
disposable `.env` from the examples and bootstrapping secrets. This runs the
existing demo fixtures and Playwright suite inside the owned project. It does
not overwrite a developer's `.env`; use a disposable checkout for that workflow.

That manual workflow first builds five local images with Bake and GitHub Actions
cache scopes (API/worker share one image). It then passes
`--prebuilt-image-prefix cluecdc-hardening-ci`, requiring locally loaded
`PREFIX-{api,web,connect,minio,bootstrap}:ci` images and starting with `--no-build`.
Build these from the candidate checkout; the ordinary local command still builds
from source. Missing images fail before stack creation. Cache misses trigger a
normal build; optional cache-export failures do not bypass any acceptance gate.
No image is pushed by this preparation job. The pinned MinIO source build is
cached rather than replaced with an unverified binary.

Security fixtures stage private files over stdin into an owned Docker volume
with explicit read-group access; Linux host `0700` temporary directories are
not bind-mounted into a different container UID. Broker/Connect containers stay
non-root with read-only fixture mounts. A stopped process fails readiness
immediately, reporting exit/OOM state. Inspect `kafka-security.json` for phase,
duration and cleanup, and the sanitized `kafka-security-diagnostics.log` on
failure; both are included in the CI artifact. Private keys/config files and
database archives remain excluded. Diagnostic timeouts do not skip cleanup.

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
