# CDC source extension: implementation and verification

Date: 2026-10-30. Branch: `feat/extend-cdc-sources`. This is an experimental
extension, not a release or production deployment. **The full production
acceptance checklist is not certified.** Oracle live E2E is NOT RUN and the
limitations below remain explicit deployment guards or follow-up work.

## A. Implementation

The existing provider registry now exposes PostgreSQL, MySQL, MariaDB, SQL Server,
MongoDB and Oracle. Shared connection persistence, credential encryption,
authentication/RBAC, audit, effective-key assessment, pipeline orchestration,
Kafka/Connect clients, operation leases, notifications and delivery engines are
reused. PostgreSQL/MySQL adapter and connector-builder implementations remain in
place; no separate pipeline engine or destination rewrite was introduced.

* MariaDB subclasses compatible MySQL wire/discovery functionality, replacing
  incompatible index, key-scan, readiness and TLS configuration behavior. It uses
  the dedicated MariaDbConnector and distinct persisted provider identity.
* SQL Server uses parameterized catalog discovery and read-only CDC diagnostics,
  with all-column capture validation, exact database identity and correct
  database-qualified topic names. LSN offsets remain owned by Debezium.
* MongoDB uses PyMongo async collection discovery/change-stream diagnostics,
  explicit document metadata, `_id`, encrypted standard/SRV URIs and raw extended
  JSON/patch envelopes. Relational upserts are blocked.
* Oracle uses python-oracledb thin metadata connections and Debezium LogMiner,
  separate CDB/PDB service settings, quoted identifiers and conservative type
  guards. No SCN reset, alternate mining adapter or administrative setup is run.
* Declarative source capabilities distinguish fixture-verified, unverified,
  conditional, unsupported and experimental behavior. API deployment checks and
  wizard delivery/snapshot validation consume these contracts.
* Existing source creation keeps PostgreSQL/MySQL's form. New sources reuse the
  existing connection dialog/fields/test controls. Connection success and CDC
  readiness remain separate; absent observations are null/Unknown.
* Additive migration `0014` widens only the connection-provider CHECK constraint.
  Existing rows/identifiers/secrets are not rewritten. Downgrade refuses to
  discard new-provider records. SQLite preservation/downgrade tests and a
  PostgreSQL offline migration plan pass; live production migration is NOT RUN.
* API dependencies add pinned pymongo/oracledb and a matching lockfile. The
  existing mssql-python driver is reused. Connect's secret-provider allowlist
  adds only `connection_uri`. Debezium remains 3.3.2.Final.
* Optional `compose.sources.yaml` profiles and a manually dispatched source CDC
  CI workflow are added. Default Compose dependencies and Kubernetes service,
  scaling and worker settings are unchanged. CI was added but not run remotely.

## B. Compatibility matrix

PASS means the cited automated/unit or live fixture scope passed, not general
production certification. PARTIAL means restricted behavior or incomplete live
coverage. NOT SUPPORTED means deliberately blocked. NOT RUN means no applicable
live evidence. No item is promoted solely because a connector class exists.

| Feature | PostgreSQL | MySQL | MariaDB | SQL Server | MongoDB | Oracle |
| --- | --- | --- | --- | --- | --- | --- |
| Connection/CRUD | PASS | PASS | PASS | PASS | PASS | PARTIAL |
| CDC readiness | PASS | PASS | PASS | PASS | PASS | PARTIAL |
| Discovery | PASS | PASS | PASS | PASS | PASS | PARTIAL |
| Initial snapshot | PASS | PASS | PASS | PASS | PASS | NOT RUN |
| Continuous streaming | PASS | PASS | PASS | PASS | PASS | NOT RUN |
| INSERT/UPDATE/DELETE | PASS | PASS | PASS | PASS | PASS | NOT RUN |
| Effective row/document key | PASS | PASS | PARTIAL | PARTIAL | PASS | PARTIAL |
| Schema evolution | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | NOT RUN |
| Recovery | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| Monitoring | PASS | PASS | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| Destination delivery | PASS | PASS | PASS | PASS | PASS | NOT RUN |
| Live E2E | PASS | PASS | PASS | PASS | PASS | NOT RUN |
| Stateful new-source delivery | Existing contract | Existing contract | PARTIAL | NOT SUPPORTED | NOT SUPPORTED | NOT SUPPORTED |
| Online collection changes/resnapshot | Existing contract | Existing contract | PARTIAL | NOT SUPPORTED | NOT SUPPORTED | NOT SUPPORTED |

Oracle connection/discovery/readiness PARTIAL denotes implemented paths and
mock/config tests; Oracle network connectivity and real catalog behavior are
not verified. Effective-key PASS for MongoDB covers `_id`, not arbitrary custom
identities. Relational non-PK/composite/custom paths have unit coverage but this
run's live fixtures use primary keys. No broad schema-change matrix was run.

New-source destination combinations:

| Source | S3/MinIO raw JSONL | PostgreSQL current state | MySQL current state | ClickHouse/BigQuery/Redshift/Synapse |
| --- | --- | --- | --- | --- |
| MariaDB | PASS on MinIO | PASS for tested CRUD/decimal/Unicode | PARTIAL: guarded mapping, live NOT RUN | NOT SUPPORTED pending verification |
| SQL Server | PASS on MinIO | NOT SUPPORTED | NOT SUPPORTED | NOT SUPPORTED |
| MongoDB | PASS on MinIO | NOT SUPPORTED | NOT SUPPORTED | NOT SUPPORTED |
| Oracle | NOT RUN | NOT SUPPORTED | NOT SUPPORTED | NOT SUPPORTED |

MinIO results do not certify AWS cloud authentication, IAM or network behavior.
Raw event archives preserve deletes and patches; they are not materialized
destination state. MariaDB's PostgreSQL test checks destination records after
each INSERT, UPDATE and DELETE and exact decimal/Unicode snapshot values.

## C. Regression and test evidence

Baseline before implementation: **475 API tests passed, 4 skipped; 89 web tests
passed across 23 files**. See [architecture audit](sources-extension-audit.md).

Final results: **518 API tests passed, 4 skipped; 94 web tests passed across
24 files.** The standard root verification chain passed, including production
build. Final API permission/TLS checks and their additional tests were also rerun
successfully. Ruff, mypy, driver imports, optional Compose and strict MkDocs
checks pass. See [machine-readable verification](sources/evidence/verification.json).

Final test commands and results are recorded with the committed fixture evidence
under [fixture evidence](sources/evidence/mariadb.json). Verification includes:

* `npm.cmd run verify`: formatting, ESLint, TypeScript, web/API tests and
  production Next.js build.
* `python -m ruff check apps/api scripts/source_e2e.py` and format check.
* `python -m mypy --config-file apps/api/pyproject.toml apps/api/app`.
* `python -m uv lock --project apps/api --check`.
* `python scripts/check-repository.py` and optional Compose config validation.
* API and Connect Docker image builds; new database-driver imports in the built
  API image; existing Java delivery/history transform assertions in Connect build.
* Migration upgrade/preservation/non-destructive downgrade tests and PostgreSQL
  offline `0013:0014` SQL generation.
* Parametrized API tests for all four new source connection CRUD, safe previews,
  pipeline creation, stored topic/key identity, secret retention/redaction,
  MongoDB upsert rejection and plugin/version validation guards.
* Negative readiness tests for unsupported servers, missing binlogs/CDC/logging,
  missing permissions, unknown metrics, incomplete capture instances, compressed
  MariaDB binlogs and unsafe resume-position settings.

Live commands (development fixtures, no existing connector configuration edited):

```sh
python scripts/source_e2e.py --source postgresql --report artifacts/source-e2e/postgresql.json
python scripts/source_e2e.py --source mysql --report artifacts/source-e2e/mysql.json
python scripts/source_e2e.py --source mariadb --stateful-postgres --outage-container cluecdc-source-fixtures-mariadb-source-1 --report artifacts/source-e2e/mariadb.json
python scripts/source_e2e.py --source sqlserver --outage-container cluecdc-source-fixtures-sqlserver-source-1 --report artifacts/source-e2e/sqlserver.json
python scripts/source_e2e.py --source mongodb --outage-container cluecdc-source-fixtures-mongodb-source-1 --report artifacts/source-e2e/mongodb.json
```

Each live runner uses the actual provider/config builder, real source database,
Kafka, Kafka Connect and existing MinIO JSONL delivery. PostgreSQL/MySQL pass
snapshot, CRUD, Unicode, decimal precision, pause/resume and connector restart
(five verified destination events each). MariaDB/SQL Server/MongoDB additionally
pass two source-container restarts, retained offsets and subsequent destination
events (seven events each). Outage recovery permits the **existing task restart**;
it does not establish fully automatic recovery. MongoDB checks ObjectId,
Decimal128, date, binary, nested documents, arrays, null and boolean values in
actual extended-JSON destination records, and requires patch updates to retain
updateDescription rather than an incomplete `after` upsert.

MariaDB's initial fixture restart test passed with legacy event positions OFF;
that was insufficient evidence of durable resume safety. The final readiness
guard requires legacy positions ON and compression OFF, as documented upstream.
The disposable fixture was corrected while retaining its original data volume,
and the final MariaDB live tests were rerun with those persistent settings.

No PostgreSQL/MySQL unit regression remains. New topics keep old-source naming.
Existing connectors were not redeployed/reconfigured and existing offsets,
topics, history and checkpoints were not removed. Test-owned connectors alone
are deleted by the runner; unique test topics, history, source tables,
destination objects and the PostgreSQL test slot remain available for inspection.
The optional source containers can be stopped without deleting their volumes.

The live runner does not claim full browser-to-API-to-worker deployment coverage;
that path has API/UI unit tests plus real adapter/connector/destination evidence.

## D. Individual deployment readiness

* **MariaDB: experimental, most complete.** Standalone 11.4 fixture passes raw
  event delivery and PostgreSQL stateful CRUD/precision/Unicode. Require
  ROW/FULL, legacy resume positions ON, compression OFF, safe keys and adequate
  retention. Replication/GTID failover, custom/composite identities, full temporal
  types and incremental signal workflows need further live qualification.
* **SQL Server: experimental event-log capture.** SQL Server 2022 Developer
  fixture passes snapshot/CRUD and repeated source interruption. Require
  DBA-enabled database/table CDC, complete capture instances, Agent/permissions,
  verified TLS and retention. Stateful delivery and online table changes are
  blocked. Other accepted server series/editions remain unverified locally.
* **MongoDB: experimental replica-set event log.** MongoDB 7 fixture passes raw
  BSON/patch delivery and repeated restarts. Standalone/sharded deployments,
  relational upserts, custom identity and collection resnapshot are blocked.
  Atlas/SRV authentication, trusted TLS, multi-member failover and expired-token
  reconciliation still need live qualification.
* **Oracle: experimental implementation only; not production-ready.** Real
  snapshot/CRUD/recovery/delivery and database/JDBC/container topology remain
  NOT RUN. The current runtime contains an Oracle JDBC jar, but complete driver
  compatibility and distribution/license review are not certified. Provision an
  approved Oracle instance and follow the [Oracle guide](sources/oracle.md).

## E. Remaining work and acceptance limits

1. Supply Oracle infrastructure/licensing and run real Oracle-to-destination
   snapshot/CRUD/restart/outage/missing-archive tests. Review full least-privilege
   CDB/PDB grants, services, quoted identifiers and driver compatibility.
2. Qualify valid/invalid trusted TLS, cloud authentication/topologies, SQL Server
   Standard/Enterprise and non-2022 versions, MariaDB 11.7 and MongoDB 6/8.
3. Run live composite/non-PK/custom-key, key-changing update, null/duplicate,
   large binary/text, full temporal and schema-change matrices. Extend support
   only when source event images and destination values remain safe.
4. Qualify MariaDB table add/remove/incremental resnapshot and signal privileges;
   implement/verify other sources' online scope changes before unblocking them.
5. Test interrupted snapshots, worker/control-plane restarts, isolated Kafka
   outage, deliberate replay/duplicates and physically expired histories. These
   were NOT RUN against the shared development broker to avoid disrupting it.
6. Add stateful SQL Server/MongoDB/Oracle or new warehouse combinations only after
   separate data-correctness evidence. Reliable full-document state reconstruction
   is not currently implemented for MongoDB patches.
7. Run the optional source CI workflow remotely and browser E2E against rebuilt
   application/worker images; deploy only after normal operator review. No main
   merge, push, release, production migration or application deployment was run.

All source setup examples are manual DBA guidance. There is no exactly-once
claim, automatic log/offset reset, implicit CDC enabling, automatic Oracle
download or unreported production-readiness promotion.

## Changed files

- `.github/workflows/source-cdc.yml`
- `README.md`
- `apps/api/app/adapters/connect.py`
- `apps/api/app/adapters/mariadb.py`
- `apps/api/app/adapters/mongodb.py`
- `apps/api/app/adapters/oracle.py`
- `apps/api/app/adapters/relational_keys.py`
- `apps/api/app/adapters/source_sql.py`
- `apps/api/app/adapters/sqlserver.py`
- `apps/api/app/api/connections.py`
- `apps/api/app/api/routes.py`
- `apps/api/app/core/errors.py`
- `apps/api/app/main.py`
- `apps/api/app/models/entities.py`
- `apps/api/app/providers/extended.py`
- `apps/api/app/providers/mariadb.py`
- `apps/api/app/providers/mongo_uri.py`
- `apps/api/app/providers/registry.py`
- `apps/api/app/providers/server_ids.py`
- `apps/api/app/providers/source_capabilities.py`
- `apps/api/app/providers/types.py`
- `apps/api/app/schemas/connections.py`
- `apps/api/app/schemas/requests.py`
- `apps/api/app/services/connections.py`
- `apps/api/app/services/destinations/service.py`
- `apps/api/app/services/pipeline.py`
- `apps/api/app/services/pipeline_tables.py`
- `apps/api/app/services/source.py`
- `apps/api/app/services/sync_strategy.py`
- `apps/api/migrations/versions/0014_extended_cdc_sources.py`
- `apps/api/pyproject.toml`
- `apps/api/tests/test_connections.py`
- `apps/api/tests/test_extended_sources.py`
- `apps/api/tests/test_source_diagnostics.py`
- `apps/api/uv.lock`
- `apps/web/src/components/wizard.tsx`
- `apps/web/src/features/connections/components/connection-create.tsx`
- `apps/web/src/features/connections/components/connection-dialog.tsx`
- `apps/web/src/features/connections/components/connection-edit.tsx`
- `apps/web/src/features/connections/components/shared.tsx`
- `apps/web/src/features/sources/components/source-create.tsx`
- `apps/web/src/features/sources/components/source-detail.tsx`
- `apps/web/src/features/sources/components/source-editor.tsx`
- `apps/web/src/lib/validation.ts`
- `apps/web/tests/e2e/connection-testing.spec.ts`
- `apps/web/tests/extended-sources.test.tsx`
- `compose.sources.yaml`
- `docs/index.md`
- `docs/sources-extension-audit.md`
- `docs/sources-extension-report.md`
- `docs/sources/README.md`
- `docs/sources/evidence/mariadb.json`
- `docs/sources/evidence/migration-0014.sql`
- `docs/sources/evidence/mongodb.json`
- `docs/sources/evidence/mysql.json`
- `docs/sources/evidence/oracle.json`
- `docs/sources/evidence/postgresql.json`
- `docs/sources/evidence/runtime.json`
- `docs/sources/evidence/sqlserver.json`
- `docs/sources/evidence/verification.json`
- `docs/sources/mariadb.md`
- `docs/sources/mongodb.md`
- `docs/sources/oracle.md`
- `docs/sources/sqlserver.md`
- `infrastructure/connect/ClueSecretConfigProvider.java`
- `mkdocs.yml`
- `packages/contracts/src/index.ts`
- `scripts/metadata_upgrade.py`
- `scripts/source_e2e.py`
