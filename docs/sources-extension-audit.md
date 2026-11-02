# Source extension audit and evidence

## Baseline (2026-10-30)

Clean starting worktree. `python -m pytest apps/api/tests -q`: **475 passed,
4 skipped** (44.10s). `npm.cmd run test:web`: **23 files, 89 tests passed**.
PowerShell blocks npm.ps1 on this host; use npm.cmd. Docker Engine 29.9.2 is
available. The existing running development stack is not an isolated test fixture.

## Architecture and dependency order

1. Extend the existing `providers` registry and metadata; leave the PostgreSQL and
   MySQL adapter/config builders in place. Add explicit verification/capabilities.
2. MariaDB: share MySQL wire/discovery code, replace incompatible metadata,
   timeout and readiness queries, use the dedicated connector. Run targeted and
   MySQL regression tests before proceeding.
3. SQL Server: read-only metadata/readiness adapter and version-specific config;
   run targeted and existing relational regression tests.
4. MongoDB: document discovery, encrypted URI, `_id`, raw event-log delivery
   guards; test without passing BSON documents through relational transformers.
5. Oracle: read-only catalog adapter, conservative LogMiner configuration and
   driver checks; explicitly record live tests that cannot run.
6. Integrate UI, additive migration, optional fixtures, security, recovery
   diagnostics, deployment/plugin checks, docs and final regression.

## Reuse and risks

* FastAPI, SQLAlchemy, Alembic metadata in `apps/api`; Next.js/React UI in
  `apps/web`; API/UI contracts in `packages/contracts`.
* `connections` is the shared persisted endpoint model. A provider CHECK
  constraint must be widened additively. JSON config and encrypted secret
  references are already reusable; old `/sources` DTOs remain compatibility APIs.
* `providers/{registry,base,mysql,postgresql}.py` already implement a lightweight
  strategy. `services/debezium.py` owns stable topic/slot/history naming.
* `services/{pipeline,pipeline_tables,snapshots}.py` own orchestration, operations,
  leases and SQL signal tables; non-SQL signal operations cannot be assumed safe.
* `services/sync_strategy.py` resolves PK, non-null unique indexes and validated
  custom keys. Its MySQL row-image assumptions require explicit MariaDB support;
  SQL Server/Oracle non-PK before images require conservative guards.
* Readiness/discovery are in `adapters/{postgres,mysql}.py`. MariaDB lacks MySQL's
  `information_schema.statistics.expression` and MAX_EXECUTION_TIME semantics.
* Existing destinations: PostgreSQL/MySQL JDBC, S3/MinIO raw JSONL, ClickHouse,
  BigQuery, Redshift and Azure Synapse history. Java `ClueDeliveryTransform` and
  warehouse transforms assume relational row maps. MongoDB `after` is extended
  JSON text and updates can be patches; only raw object-storage envelopes are
  initially eligible. Destination compatibility must be checked before deployment.
* Source topics currently use prefix.schema.table. SQL Server's default includes
  the database name too; new SQL Server pipelines need explicit matching topic
  calculation without changing existing source names.
* Authentication/RBAC is centralized in route dependencies, audit in metadata
  repository, credentials in `services/secrets.py`; use these for all sources.
  URI secrets must never enter config JSON or validation errors.
* Existing tests include API/SQLite mocks, provider tests, UI Vitest, Playwright,
  Java transform tests, compose/deployment checks and real acceptance scripts.
  Passing mocked tests does not establish CDC correctness.
* Runtime: `quay.io/debezium/connect:3.3.2.Final`, Kafka broker 4.1.2. Read-only
  `/connector-plugins` on the existing worker reports MariaDbConnector,
  SqlServerConnector, MongoDbConnector and OracleConnector at **3.3.2.Final**;
  Kafka Connect bundled libraries report 4.1.0. No runtime upgrade is necessary.
  Oracle JDBC availability must be checked separately from plugin discovery.
* Compose defaults and Kubernetes use the shared Connect image. New database
  fixtures must remain optional. No implementation/migration may change existing
  connectors, topics, offsets, schema history or checkpoints.

Final results and limitations are recorded in `sources-extension-report.md`.
