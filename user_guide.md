# ClueCDC user guide

ClueCDC captures PostgreSQL changes into Kafka and delivers them to PostgreSQL destinations through Kafka Connect. Use the web interface to register databases, choose tables, deploy capture and delivery connectors, inspect events, and operate each runtime independently.

```text
PostgreSQL source → Capture pipeline → Kafka topics → Delivery → PostgreSQL destination
                                                      └──────→ Another destination
```

A **source** is the database you capture from. A **pipeline** selects its tables and Kafka topics. A **destination** is a target database. A **delivery** connects a pipeline's topics to destination tables and has its own sink connector. A pipeline can have several deliveries; pausing one delivery does not pause capture.

## 1. Open the local workspace

From the repository folder, start the services if needed:

```powershell
python scripts/bootstrap.py
docker compose up -d --wait --wait-timeout 180
docker compose ps
```

For a first installation or after application changes, use `docker compose up -d --build --wait --wait-timeout 180` instead. Docker with Compose v2 and Python 3.12+ are required. Node is needed only for local frontend development/tests.

Open [ClueCDC](http://localhost:3000). A reset workspace has no registered clusters, sources, pipelines, destinations, events or audit history. The local databases keep their table definitions and database roles so you can start a new workflow. The reset performed for this guide also cleared the source's sample rows.

The default local developer mode opens as Admin without a login. For an installation configured with token authentication, enter your administrator-issued bearer token in **Settings**. Roles restrict operations; Viewer is read-only. Deployment/authentication details are in [local setup](docs/development/local-setup.md).

Keep `.env` private. It contains generated credentials and encryption keys. Open it in your local editor to copy a password into the relevant form; this guide uses variable names, never actual passwords. Bootstrap preserves existing credentials. Changing `.env` does not rotate passwords in already initialized databases.

### Local connection addresses

Use the **container address** in ClueCDC forms. The API and connectors connect from inside Docker, where `localhost` points to their own container.

| Service                | Address to enter in ClueCDC | Address for host tools  |
| ---------------------- | --------------------------- | ----------------------- |
| Kafka                  | `kafka:29092`               | `localhost:9092`        |
| Kafka Connect          | `http://kafka-connect:8083` | `http://localhost:8083` |
| Source PostgreSQL      | `cdc-source-postgres:5432`  | `localhost:5434`        |
| Destination PostgreSQL | `destination-postgres:5432` | `localhost:5435`        |

## 2. Register Kafka and Kafka Connect

1. Open **Streaming → Kafka Clusters** and add a cluster.
2. Name it **Local Kafka**, set bootstrap servers to `kafka:29092`, and choose **PLAINTEXT** for this local stack. Save.
3. Open **Platform → Connect Clusters** and add a cluster.
4. Name it **Local Connect**, set its URL to `http://kafka-connect:8083`, select **Local Kafka**, and save.

Kafka Connect must belong to the same Kafka cluster selected by the pipeline. Registration alone does not prove runtime health. Runtime observations and actual topic requests update operational status.

## 3. Register and discover the source

Open **Data Movement → Sources → Add source** and enter:

| Field    | Local value                                    |
| -------- | ---------------------------------------------- |
| Name     | `Commerce PostgreSQL`                          |
| Type     | PostgreSQL                                     |
| Host     | `cdc-source-postgres`                          |
| Port     | `5432`                                         |
| Database | `commerce`                                     |
| Username | `cdc_user`                                     |
| Password | Value of `SOURCE_PASSWORD` from private `.env` |
| SSL      | Off for the supplied local database            |

Register the source, open its detail page, and:

1. Click **Test connection** and check the result.
2. Open **CDC Readiness** and review replication/WAL and table requirements.
3. Click **Discover tables** and wait for discovery to complete.
4. Inspect `public.customers`, `public.orders` and `public.payments`, including columns and primary keys.

The local source already has logical replication configured and uses replica identity FULL. Empty tables can still be discovered and selected. For your own PostgreSQL database, follow the readiness guidance and [PostgreSQL source operations](docs/connectors/postgresql.md); ClueCDC does not automatically apply the readiness remediation SQL.

## 4. Create a capture pipeline

Open **Data Movement → Pipelines → Create pipeline**. Follow the six steps:

1. **Source:** select Commerce PostgreSQL.
2. **Tables:** select `public.customers` and `public.orders`.
3. **Capture:** name the pipeline **Commerce capture**, use the unused topic prefix `commerce`, and keep **Initial snapshot, then stream changes**. Leave advanced settings at their defaults for this walkthrough.
4. **Kafka:** select Local Kafka and Local Connect.
5. **Review:** inspect the table selection and generated topics: `commerce.public.customers` and `commerce.public.orders`. Advanced configuration redacts credentials.
6. **Deploy:** save and deploy the capture connector.

Open the pipeline's **Connector** tab. Wait for actual state and its task to be **RUNNING**. Initial **UNKNOWN** can mean the first observation is pending. Deployment prepares the selected capture topics using Kafka broker defaults, including for empty source tables. Use **Streaming → Topics** to inspect the actual topics. With empty source tables, there may be no row events until you insert data.

The **Overview** tab shows source, capture, Kafka and destination associations. **Events** inspects actual Kafka records. **Tables**, **Topics**, **Snapshot**, **Schema**, **Configuration**, **Logs** and **Activity** provide configuration and operational context. A READ event is a snapshot row; it does not establish that the whole snapshot has completed.

## 5. Add a PostgreSQL destination

Open the pipeline's **Destinations** tab and click **Add destination** to preselect the pipeline. You can also start at **Data Movement → Destinations → Add destination**.

Complete these six steps:

1. **Destination:** select a saved target from the destination dropdown to reuse its connection and proceed directly to **Delivery source**. To create a target, choose **Add a new destination**, then PostgreSQL. Other types are disabled as Coming Soon.
2. **Connection:** enter the values below and click **Test connection**. Continue after success. Continuing saves an encrypted logical target; delivery is still unconfigured until deployment succeeds.
3. **Delivery source:** select Commerce capture and both `commerce.public.*` topics.
4. **Mapping:** map customers to `public.customers` and orders to `public.orders`. Automatic mapping can populate these names; review them before continuing.
5. **Options:** keep **Upsert**, **Record key**, and deletes enabled for this walkthrough. See the schema policy below before choosing auto create/evolve.
6. **Review:** click **Validate and review**, inspect the checks/configuration, then **Deploy destination**.

| Connection field | Local value                                         |
| ---------------- | --------------------------------------------------- |
| Name             | `Analytics PostgreSQL`                              |
| Environment      | DEV                                                 |
| Host             | `destination-postgres`                              |
| Port             | `5432`                                              |
| Database         | `analytics`                                         |
| Username         | `delivery_user`                                     |
| Password         | Value of `DESTINATION_PASSWORD` from private `.env` |
| SSL              | Off for the supplied local database                 |

Validation checks real topics, discovered source keys/types, target connectivity/permissions, and the installed JDBC plugin/configuration. Missing schemas must be created by a database operator. The local destination already has public customers/orders tables and an `analytics` schema.

### Choose table creation and evolution

| Auto create | Auto evolve | Result                                                       |
| ----------- | ----------- | ------------------------------------------------------------ |
| On          | On          | JDBC can create missing tables and add supported columns     |
| On          | Off         | Deployment creates missing tables; JDBC does not evolve them |
| Off         | Off         | Existing compatible tables are required                      |
| Off         | On          | Invalid: evolution requires auto create                      |

For the pre-created public tables, you can disable both options. For new tables in the existing `analytics` schema, enable auto create; enable evolution only if you want supported column additions. Foreign keys/defaults are not copied, and arbitrary type changes are not migrated. Upsert handles replay by record key; delivery remains at least once. Insert mode can fail on replay.

Open the destination's **Connector** tab and wait for its delivery/task to be **RUNNING**. Its connection health is separate from delivery state. See [destination operations](docs/connectors/postgresql-destination.md) for supported types and schema limitations.

## 6. Verify actual delivery

Run these commands from the repository in PowerShell. They create sample rows; the database was left empty after the reset. Use a new email if you have already inserted this customer.

### Insert source rows

```powershell
@'
INSERT INTO public.customers(name,email)
VALUES ('Guide customer','guide.customer@example.test');
INSERT INTO public.orders(customer_id,total)
SELECT id,12.34 FROM public.customers
WHERE email='guide.customer@example.test';
'@ | docker compose exec -T cdc-source-postgres psql -U postgres -d commerce -v ON_ERROR_STOP=1
```

Open **Pipeline → Events**, select the customers or orders topic, and **Fetch recent events**. Inspect CREATE records, keys, partition and offset.

Query the independent destination database:

```powershell
@'
SELECT c.id,c.name,c.email,o.total,o.status
FROM public.customers c
LEFT JOIN public.orders o ON o.customer_id=c.id
WHERE c.email='guide.customer@example.test';
'@ | docker compose exec -T destination-postgres psql -U postgres -d analytics -v ON_ERROR_STOP=1
```

Run the query again after a few seconds if delivery is pending. Expect the customer, total `12.34`, and status `pending`. This SQL result proves a write; connector RUNNING alone does not.

### Update and inspect before/after

```powershell
@'
UPDATE public.customers SET name='Guide customer updated',updated_at=now()
WHERE email='guide.customer@example.test';
UPDATE public.orders SET total=45.67,status='paid',updated_at=now()
WHERE customer_id=(SELECT id FROM public.customers WHERE email='guide.customer@example.test');
'@ | docker compose exec -T cdc-source-postgres psql -U postgres -d commerce -v ON_ERROR_STOP=1
```

Fetch events again and select UPDATE to inspect before/after and changed fields. Repeat the destination query; expect the updated name, total `45.67`, and status `paid`.

### Delete the sample rows

```powershell
@'
DELETE FROM public.orders
WHERE customer_id=(SELECT id FROM public.customers WHERE email='guide.customer@example.test');
DELETE FROM public.customers WHERE email='guide.customer@example.test';
'@ | docker compose exec -T cdc-source-postgres psql -U postgres -d commerce -v ON_ERROR_STOP=1
```

Inspect DELETE events and repeat the destination query. With deletes enabled, it should return no rows. Null tombstones are ignored; the CDC delete envelope performs deletion.

## 7. Add another destination or delivery

To try fan-out, add **Reporting PostgreSQL** using the same local destination connection but map to `analytics.customers` and `analytics.orders`, with auto create enabled. These are separate tables from the first delivery. Both logical destinations use the independent target container in this local example; real deployments can use separate target databases.

Each delivery has its own connector and consumer offsets. New deliveries start at the earliest retained Kafka offsets, which can include older changes. Kafka retention limits available history. Two deliveries on the same logical destination cannot claim the same target table. Separate logical destinations can point to the same database, so review table mappings to avoid unintended overlapping writes.

To connect another pipeline to an existing destination, open the destination's **Overview** and choose **Add delivery**. Select the pipeline, topics and distinct table mappings.

## 8. Operate and troubleshoot

### Destination detail tabs

| Tab           | Use                                                                                      |
| ------------- | ---------------------------------------------------------------------------------------- |
| Overview      | Connection health, aggregate delivery state, connected pipelines and Add delivery        |
| Mappings      | Inspect/edit topic-to-table mappings; updates revalidate and apply runtime configuration |
| Delivery      | Inspect options and pause/resume/restart or remove individual deliveries                 |
| Connector     | Actual connector/tasks, sanitized failures, task restart and redacted configuration      |
| Configuration | Inspect saved connection and delivery settings with secrets redacted                     |
| Activity      | Review meaningful actions and related delivery audits                                    |

Changing a mapping affects subsequent writes and preserves offsets; it does not backfill old records into the new table. After source schema changes, rediscover the source and update delivery mappings to refresh the sink's schema metadata. Unsupported types/record shapes fail explicitly.

### Pause, resume and restart

- **Pause capture** stops new capture events. Existing Kafka records may still be delivered by sinks.
- **Pause delivery** holds that sink while capture and other sinks can continue.
- **Resume delivery** consumes retained backlog; changes become visible in its target.
- **Restart** restarts the selected connector/tasks. Repair the cause of a failure first.

Destination header controls apply to its deliveries; controls in **Delivery** operate one delivery. **Connector → Restart task** operates one sink task. Desired state records the requested state; actual state comes from Kafka Connect and can take a refresh to change.

| Actual state | Meaning                                                                   |
| ------------ | ------------------------------------------------------------------------- |
| RUNNING      | Connector and observed tasks are running; verify target writes separately |
| PAUSED       | Runtime is paused                                                         |
| DEGRADED     | Connector is running but a task failed                                    |
| FAILED       | Connector failed                                                          |
| UNKNOWN      | Observation pending, missing tasks/connector, or runtime unavailable      |

### Overview, errors and audit

**Overview** compares capture and delivery states and shows topology, destination health, active incidents, schema changes and meaningful activity. Toggle **Auto refresh** or use **Refresh** to update observations. Historical throughput, lag and event freshness display **Unavailable** until a metrics provider is configured; the time range selector stays disabled.

Use **Ctrl/Cmd+K** or the top-bar search to find sources, pipelines, destinations, topics, managed connectors and schemas. Enter at least two characters, choose the registered Kafka cluster for topic search, then use **Tab** and **Enter** to open results. **Esc** closes search and detail drawers. Tables support search, sorting, column visibility and pagination; focus a resource row and press **Enter** to inspect it. Detail tabs support the arrow keys, Home and End.

**Operations → Monitoring** separates platform, capture, delivery and task-failure observations. **Operations → Errors** supports state, category, severity, pipeline and destination filters. Use **Acknowledge** to mark investigation and **Resolve** after repair. These buttons update incident records, not connector health. Observed recovery resolves runtime incidents. **Operations → Audit** retains the full action history and opens field changes in a drawer; access depends on your role.

Throughput, exact lag, snapshot completion and last successful delivery show **Unavailable** until a metrics provider is connected. This is expected. JDBC can retain an initial processing exception until subsequent input causes Connect to report task failure, so use actual target queries when verifying writes.

| Problem                                      | Next action                                                                                  |
| -------------------------------------------- | -------------------------------------------------------------------------------------------- |
| SOURCE_AUTH_FAILED / DESTINATION_AUTH_FAILED | Check username/password and initialized database roles                                       |
| SOURCE_UNAVAILABLE / DESTINATION_UNAVAILABLE | Check container hostname, service health and SSL settings                                    |
| SINK_PLUGIN_MISSING                          | Inspect the chosen Connect cluster; install the JDBC plugin/driver in that runtime           |
| Missing topic                                | Deploy capture first. For an existing capture, click **Prepare capture topics** during delivery validation; this creates only missing configured topics and retries validation. Kafka creation permissions are required. |
| Primary-key/schema mismatch                  | Match target keys/columns to source discovery or use reviewed creation/evolution options     |
| DEGRADED or FAILED                           | Open Connector, inspect sanitized task errors, repair target/source conditions, then restart |
| No recent events                             | Check selected topic, generate a fresh change, and fetch again; inspection is bounded        |
| FORBIDDEN                                    | Ask your administrator for a role with the required operation permission                     |

If a source gains a column and delivery becomes **DEGRADED**, **Auto evolve schema** alone does not refresh ClueCDC's saved source types. Run source discovery again, open the destination's **Mappings** tab, choose **Edit mappings**, and save the existing mappings to refresh delivery metadata. Then restart the failed task from **Connector**. With Auto evolve enabled and the required target permissions, the sink can add the new destination column when it processes the change. Type changes, removed columns, and primary-key changes require separate review.

For service diagnostics:

```powershell
docker compose ps
docker compose logs --tail 100 cluecdc-api kafka-connect
```

Review logs locally before sharing; database/connector logs can contain row details.

## 9. Remove resources in dependency order

`PIPELINE_IN_USE` and `DESTINATION_IN_USE` are expected HTTP 409 dependency checks. A delivery links a pipeline and destination; remove that link before either parent.

1. Open **Destination → Delivery → Remove delivery** and confirm. This deletes its sink connector and association.
2. To delete a pipeline, remove **all** its deliveries across destinations, then delete the pipeline.
3. To delete a destination, remove **all** deliveries attached to it, then delete the destination.
4. Delete sources after their pipelines, and infrastructure clusters after their dependent pipelines/connectors and associations are removed.

Pausing does not remove a dependency. Delivery/destination deletion retains target tables/data. Pipeline deletion retains Kafka topics and source replication slots/publications. An operator must review database/Kafka cleanup separately; see [source operations](docs/connectors/postgresql.md).

## 10. Optional automated walkthrough

To populate an empty local workspace and run the full real capture/fan-out acceptance:

```powershell
python scripts/demo-destination.py --api-url http://127.0.0.1:3000/api/v1
```

This creates/reuses Local Kafka, Local Connect, Commerce PostgreSQL, Commerce capture, Analytics PostgreSQL and Reporting PostgreSQL. It checks fresh inserts/updates/deletes, independent pause/catch-up, restart and audits, and leaves the connectors running for exploration. It adds application configuration and audit/event history; it is not part of the empty reset. Sanitized results are written under `artifacts/`. `CLUECDC_TOKEN` supplies bearer authentication when needed.

For capture only, run `python scripts/demo.py --api-url http://127.0.0.1:3000/api/v1`. For credential checks, run `python scripts/verify-secrets.py --api-url http://127.0.0.1:3000/api/v1`.

## 11. Stop or reset the local environment

Stop while preserving data:

```powershell
docker compose down
```

**Destructive local reset:** the following removes this Compose project's metadata, source/destination data, Kafka records, connector configuration/offsets and source slots/publications. Use it only when you intend to erase this local workspace. It does not delete data in separately configured external databases/Kafka clusters.

```powershell
docker compose down --volumes --remove-orphans
docker compose up -d --wait --wait-timeout 180
```

Startup recreates database structures/roles and seeds the local source with sample rows. To leave those source tables empty too:

```powershell
docker compose exec -T cdc-source-postgres psql -U postgres -d commerce -v ON_ERROR_STOP=1 -c 'TRUNCATE TABLE public.payments, public.orders, public.customers RESTART IDENTITY;'
```

The local destination's initialization creates empty tables. Keep `.env` for the existing local credentials/settings. Refresh the browser after resetting. Generated screenshots/reports under `artifacts/` and browser test results are ordinary files and remain outside Docker volumes.

## Further reference

- [README](README.md): project overview, startup, checks and release limits.
- [Local setup](docs/development/local-setup.md): deployment variables and authentication.
- [PostgreSQL source](docs/connectors/postgresql.md): readiness and replication cleanup.
- [PostgreSQL destination](docs/connectors/postgresql-destination.md): schema policy, supported types and delivery limits.
- [API notes](docs/api/overview.md) and [interactive API reference](http://localhost:8000/docs).

This Compose environment is for local development. Production requires secured networks/integrations, reviewed permissions, backups and capacity planning; the current runtime worker supports one API replica.
