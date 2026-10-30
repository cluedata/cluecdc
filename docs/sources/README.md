# CDC sources

ClueCDC retains its existing PostgreSQL and MySQL providers. MariaDB, SQL Server,
MongoDB and Oracle are additive, **experimental** sources. See the
[verification report](../sources-extension-report.md) before deployment. A
successful connection test does not establish CDC readiness or destination
correctness. API/UI metadata exposes these distinctions through
`source_capabilities`, including verified, unverified, conditional, unsupported
and experimental features.

## Runtime and versions

The Connect image remains Debezium **3.3.2.Final**, with its existing Kafka Connect
4.1.0 libraries; the broker remains Kafka 4.1.2. No PostgreSQL/MySQL plugin upgrade
or connector rewrite is included. New sources require the installed plugin to
report 3.3.2.Final. Changing that guard requires fresh compatibility evidence.

| Source | Accepted database series | Source connector class suffix | Delivery policy |
| --- | --- | --- | --- |
| PostgreSQL | Existing contract | `postgresql.PostgresConnector` | Existing destinations unchanged |
| MySQL | Existing contract | `mysql.MySqlConnector` | Existing destinations unchanged |
| MariaDB | 11.4.x, 11.7.x | `mariadb.MariaDbConnector` | Raw S3/MinIO; conditional PostgreSQL/MySQL current state |
| SQL Server | 2017, 2019, 2022; CDC-capable edition | `sqlserver.SqlServerConnector` | Raw S3/MinIO only |
| MongoDB | 6.x, 7.x, 8.x replica sets | `mongodb.MongoDbConnector` | Raw S3/MinIO only |
| Oracle | 19c, 21c; LogMiner | `oracle.OracleConnector` | Raw S3/MinIO; live verification NOT RUN |

All classes begin with `io.debezium.connector.`. Accepted versions are narrower
than potential engine compatibility; they do not imply that every version was
tested here. See the [upstream 3.3 tested-version matrix](https://debezium.io/releases/3.3/).

## Setup and API

Use **Sources → Add source** or the existing connection dialog. PostgreSQL/MySQL
retain their original form. Choosing a new source uses the shared connection
fields, encrypted secret store, test control, RBAC and audit services.

* [MariaDB](mariadb.md)
* [SQL Server](sqlserver.md)
* [MongoDB](mongodb.md)
* [Oracle](oracle.md)

New provider names are `MARIADB`, `SQLSERVER`, `MONGODB`, `ORACLE` on
`/api/v1/connections`; all are source-only. Relational endpoint configuration uses
`host`, `port`, `database_name`, `username`, `ssl_enabled`, `environment`, and
`provider_options`. Passwords go in `credentials.password`. MongoDB's full URI
goes exclusively in `credentials.connection_uri`. Leaving credentials empty on
an update retains encrypted credentials. Provider/category remain immutable.

`GET /sources/{id}/cdc-readiness?connect_cluster_id={uuid}` checks database
prerequisites and the selected worker plugin. Without a cluster selection,
plugin availability is **Unknown**, with an actionable warning. Deployment also
validates the actual connector, including driver/connectivity errors. Raw
validation messages for experimental plugins are withheld because they can
contain resolved credentials. `GET /sources/{id}/databases` lists visible
databases for new adapters; existing namespace APIs remain intact.

MongoDB discovery returns explicit `collection_kind=DOCUMENT`, BSON `_id`
identity and patch semantics in collection metadata. Compatibility `schema_name`
and `table_name` fields carry database/collection names; collections are never
queried with SQL or inferred to have relational columns.

## Capture, keys and delivery

New sources accept `initial` (snapshot then streaming) or `no_data` (future
changes). Other snapshot modes and arbitrary Debezium overrides are rejected,
including attempts to change converters, offsets, adapters or credentials.
Existing source modes and connector configurations are preserved.

Relational key ordering remains primary key, eligible non-null enforced unique
key, then validated custom key. Composite identities use the shared resolver.
Bounded custom-key scans reject NULLs and duplicates; a scan without an enforced
constraint does not establish future uniqueness. MariaDB requires ROW/FULL
before images. SQL Server/Oracle non-primary identities are ineligible for
current-state synchronization until before-image behavior is verified. MongoDB
always uses `_id`. Keyless relational capture can preserve raw event envelopes.

The wizard blocks unsupported relational delivery before creation. For SQL
Server/MongoDB/Oracle choose capture only, then add an object-storage delivery.
S3/MinIO preserve complete envelopes, delete events, keys and Kafka offsets in
JSONL. They are event archives, not materialized state synchronization. MongoDB
patches are never converted to incomplete upserts. ClickHouse, BigQuery,
Redshift and Synapse combinations with new sources are blocked pending separate
verification; their existing PostgreSQL/MySQL contracts remain unchanged.

MariaDB reuses SQL signal-table orchestration for add/remove/resnapshot, with
the existing PK requirement and additional signal-table privileges. Its signal
workflows remain conditional pending live coverage. Online collection changes
and per-collection resnapshot for SQL Server/MongoDB/Oracle are explicitly
blocked before queuing metadata jobs or editing connector filters. Pause,
resume, task restart, monitoring and connector deletion use the shared lifecycle.

## Durability and recovery

Kafka Connect/Debezium owns binlog positions, LSNs, MongoDB resume tokens and
Oracle SCNs. ClueCDC does not create a second checkpoint system. Source outage
tests use the existing task restart when necessary; this is not proof of
automatic recovery from every failure. At-least-once replay is expected.

Expired resume tokens, missing Oracle archive logs and unavailable SQL Server
LSNs surface `SOURCE_RECOVERY_REQUIRED`, category `RECOVERY_REQUIRED`, without
retrying an offset reset. Restore retained history if possible; otherwise obtain
explicit approval for a reconciliation plan and isolated resnapshot. Never
delete schema history, reset offsets or recreate connectors to mask missing data.
Operators must reconcile retained destination data before resuming delivery.

Initial snapshot notifications use the existing monitoring path. Missing source
size, lag or retention observations remain null/Unknown. No new zero-valued
metrics are synthesized. Database/worker/Kafka failures retain existing alerts.

## Deployment and fixtures

Apply additive migration `0014` using the existing backup/upgrade process. It
widens only the connection-provider CHECK constraint. Downgrade refuses to
discard new-source records. No migration contacts Kafka or changes connectors.

Build updated API/worker and Connect images. The Connect change only extends
the secret-provider allowlist with `connection_uri`; existing extensions and
plugins are retained. The inspected upstream runtime already contains
`ojdbc11-21.15.0.0.jar`. Plugin discovery alone does not establish Oracle driver
or database compatibility. This change adds no proprietary driver binary;
operators remain responsible for their database and driver distribution terms.

Kubernetes uses the same services, worker counts and Connect configuration.
Deploy locally built images via your normal image overrides; no release image
or production deployment is created here. The shipped policies constrain
ingress, so no additional egress rule is necessary. Environments with separate
egress controls must permit source ports (3306/1433/27017/1521), SRV DNS and
configured TLS endpoints. Keep secret-service traffic private and authenticated.

`compose.sources.yaml` is a standalone **optional** fixture file. It adds no
default startup dependency and uses loopback-only host ports. SQL Server's
profile explicitly accepts its Developer EULA for local testing. Oracle must
be supplied by an operator with appropriate licensing.

```sh
docker compose -p cluecdc-source-fixtures -f compose.sources.yaml --profile sources-mariadb up -d
docker compose -p cluecdc-source-fixtures -f compose.sources.yaml --profile sources-sqlserver up -d
docker compose -p cluecdc-source-fixtures -f compose.sources.yaml --profile sources-mongodb up -d
```

The live runner uses an existing **development test** Kafka/Connect/MinIO stack
on the `cluecdc_default` network. Attach each disposable source to that network:

```sh
docker network connect cluecdc_default cluecdc-source-fixtures-mariadb-source-1
docker network connect cluecdc_default cluecdc-source-fixtures-sqlserver-source-1
docker network connect cluecdc_default cluecdc-source-fixtures-mongodb-source-1
python scripts/source_e2e.py --source mariadb --stateful-postgres --outage-container cluecdc-source-fixtures-mariadb-source-1 --report artifacts/source-e2e/mariadb.json
python scripts/source_e2e.py --source sqlserver --outage-container cluecdc-source-fixtures-sqlserver-source-1 --report artifacts/source-e2e/sqlserver.json
python scripts/source_e2e.py --source mongodb --outage-container cluecdc-source-fixtures-mongodb-source-1 --report artifacts/source-e2e/mongodb.json
```

The runner explicitly initializes fixture CDC/replication and creates unique
tables/collections, topics, connectors and buckets. It reads fixture secrets
without printing them. It removes only connectors it successfully created;
test tables, topics, schema history, offsets and destination objects are retained
for inspection. Repeated-outage mode accepts only the fixed disposable fixture
container names. It never restarts a shared broker or existing source database.
Evidence is intentionally scoped to adapters, generated configs, real capture
and destinations, rather than claiming a full browser-driven deployment test.
