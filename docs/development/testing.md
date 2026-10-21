# Testing

## GitHub Actions

Automatic CI on pushes to `main`/`master` and pull requests selects gates from
the changed files. Repository safety and Git-history secret scanning always run.
The stable **CI checks** job fails if any selected job fails or is cancelled;
use it as the required branch-protection check instead of individual conditional
jobs. Superseded runs on the same PR/branch are cancelled automatically.

| Change | Selected checks |
| --- | --- |
| Markdown/documentation | Strict MkDocs build; no application images or fixtures |
| Web unit tests | Web format, lint, types and unit tests |
| API unit tests | API lint, types and tests with real PostgreSQL worker concurrency |
| CSS or web public assets | Web quality plus native AMD64 runtime/asset smoke on PRs; both architectures on default-branch pushes |
| Application or integration code | Relevant quality checks and native AMD64 image builds/core smoke; ARM64 selected by the policy below; CDC/S3/browser integration once on AMD64 |
| Connector, Docker/Compose or native dependency code | Native AMD64 and ARM64 image builds/core smoke |
| Compose/Docker configuration | Compose rendering on Ubuntu, macOS and Windows, including CI overrides |
| Kubernetes configuration | Deployment contract and strict Kubernetes schemas |
| Dependency manifests/lockfiles | Corresponding npm or Python dependency audit |
| Workload generator | Native image/dependency checks on both architectures; real database regression tests on AMD64 |

ARM64 uses a native `ubuntu-24.04-arm` runner, not QEMU. To reduce the PR critical
path, ordinary application/UI changes run the container job on AMD64 only.
Changes to Docker/Compose/environment inputs, fixtures/connectors, dependency
manifests/lockfiles, native source files or the workload generator also select
ARM64. Every relevant push to `main`/`master` and every full manual CI run keeps
both architectures. Thus application-only PRs defer ARM64 smoke until after
merge; they do not claim pre-merge ARM64 verification. Documentation/unit-test-only
changes still skip runtime containers on both architectures. The change selector
emits the native runner matrix explicitly, with regression coverage for PR,
default-branch, mixed-file and fail-closed selections.

Weekly scheduled runs repeat both dependency audits even without source changes,
so new advisories are still checked. They do not start runtime/test stacks and
use a separate concurrency group so they cannot cancel a push/PR validation.

Workflow changes and unknown build configuration select every gate. Deleted
and renamed files are included; an unavailable comparison base also selects
every gate. `scripts/ci_changes.py` implements this policy and has regression
tests for skipped infrastructure and fail-closed comparisons.

Manual CI with `run_full_ci=true` selects every gate; with it disabled, only
documentation and repository safety checks run. Release validation still runs
all quality/dependency gates regardless of changed paths, followed by the full
hardening and Kubernetes acceptance gates.

Runtime images are built once with Bake and per-image/per-architecture GitHub
Actions caches. API, worker and MinIO bootstrap share one API image through
`.github/compose.ci.yaml` and `.github/compose.test.ci.yaml`. Core startup uses
`--no-build`; relevant AMD64 runs add test fixtures to the existing stack without
an intermediate `down`. ARM64 does not install host npm/pip application tooling
or repeat MinIO, CDC, S3 or Playwright acceptance. Its core HTTP/asset, native
image optimization and migration smoke checks remain enabled whenever selected. The
optional commerce image is built only for workload changes or an explicit full
run. Failure diagnostics are redacted and uploaded only when needed; full release
hardening retains its success reports. Browser/demo seed scripts remain
necessary fixtures and are not obsolete.

The supplied ARM64 job log took about 524 seconds: fixture startup/build 152s,
core startup/build 77s, standalone web/API builds 69s, S3 acceptance 65s, and
browser installation/tests 77s. Those are the measured old baseline, not a
promise of the new duration. Cold caches still download/build dependencies;
measure a fresh GitHub run and a warm-cache repeat after merging this change.

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

The CI invocation also supplies `--env-file .env --browser` after creating a
disposable `.env` from the examples and bootstrapping secrets. This runs the
existing demo fixtures and Playwright suite inside the owned project. It does
not overwrite a developer's `.env`; use a disposable checkout for that workflow.

CI first builds five local images with Bake and independent GitHub Actions
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
