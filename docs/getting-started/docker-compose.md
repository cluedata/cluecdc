# Docker Compose

ClueCDC uses two primary configurations on Linux containers (amd64 or arm64):

| Configuration | Services |
| --- | --- |
| `docker-compose.yml` | `metadata-db`, `kafka`, `kafka-connect`, `cluecdc-api`, `cluecdc-worker`, `cluecdc-web` |
| `docker-compose.demo.yml` | Extends local with `postgres-demo`, `mysql-demo`, and optional fixtures/tools |

The worker is required for discovery jobs, reconciliation, delivery operations,
Data Quality, observability, and alerts. The local topology contains no demo databases.

## Local startup

Install [prerequisites](prerequisites.md), including host Python for bootstrap:

```bash
python scripts/bootstrap.py
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

Open `http://localhost:3000/login`; development credentials are
`admin@cluecdc.local` / `cluecdc-admin`. Technical secrets are generated independently.
Register bundled Kafka at `kafka:29092` and Connect at `http://kafka-connect:8083`.
All published ports bind to `127.0.0.1`. Local metadata is accessible through
`docker compose exec metadata-db psql -U cluecdc -d cluecdc`.

## Demo and integration tests

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build --wait
```

Each engine keeps one persistent server with separate source/destination databases
and roles. This retains the existing DNS names and volumes without duplicating servers.
Initialization runs only on empty volumes and seeds customers, orders, payments,
keyed/keyless examples, and composite unique-key fixtures.

### Fixture connections and credentials

| Role | Compose host/port | Host port | Database | User |
| --- | --- | --- | --- | --- |
| PostgreSQL source | `postgres-demo:5432` | 5434 | `cluecdc_source` | `source_user` |
| PostgreSQL destination | `postgres-demo:5432` | 5434 | `cluecdc_destination` | `destination_user` |
| MySQL source | `mysql-demo:3306` | 3307 | `cluecdc_source` | `source_user` |
| MySQL destination | `mysql-demo:3306` | 3307 | `cluecdc_destination` | `destination_user` |

Fresh installations use `CLUECDC_LOCAL_PASSWORD` for these roles and database
administrators (`postgres` / `root`). Legacy per-role overrides take precedence.
The demo also publishes metadata on loopback port 5433 (`METADATA_PORT`).
Use SQL clients or existing `psql`/`mysql` commands for inspection.

## Optional profiles

| Profile | Capability |
| --- | --- |
| `tools` | `commerce-generator` workload container |
| `object-storage` | MinIO and one-shot `minio-bootstrap` bucket initializer |
| `clickhouse` | Optional ClickHouse destination fixture |
| `sources-mariadb` / `sources-sqlserver` / `sources-mongodb` | Experimental source fixtures |

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml --profile object-storage --profile tools up -d --build --wait
docker compose -f docker-compose.yml -f docker-compose.demo.yml run --rm commerce-generator python generate.py status
python scripts/demo.py
python scripts/demo-destination.py --skip-capture-demo
python scripts/demo-object-storage.py
```

MinIO bootstrap must finish before running storage demonstrations/tests; `--wait`
waits for the one-shot job. The API's optional completion dependency orders this
bootstrap only when the storage profile is enabled. MinIO uses access key `minioadmin`, the shared local
password unless overridden, endpoint `http://minio:9000`, and bucket `cluecdc-cdc`.
It remains an optional AGPL dependency built from pinned source. ClickHouse uses
`CLICKHOUSE_TEST_PORT` / `CLICKHOUSE_TEST_PASSWORD` overrides. Optional experimental
sources retain their existing fixture credentials; consult the source guides.
Cloud warehouses and Oracle remain operator-provided.

The optional [metrics overlay](../operations/monitoring.md) changes the Connect
image for trusted exporters. CI image overlays supply image aliases only, with
no additional test topology. All integration/E2E checks reuse this demo extension;
see [testing](../development/testing.md) for isolated commands and cleanup.

## Storage, upgrades, and troubleshooting

Metadata, Kafka (including Connect configuration/offset/status topics), and demo
databases retain their existing named volumes. Keep the same Compose project name
(default `cluecdc`). File renaming alone does not rename those volumes.
`docker compose down` stops the project and preserves data. Never use volume
removal as an upgrade procedure. Unused historical volumes can remain untouched.

Bootstrap preserves established credentials and keys. Changing an environment
variable never rotates a password inside an initialized database. Follow
[existing-installation guidance](../operations/configuration.md#existing-installations)
and [backup/restore](../operations/backup-restore.md).

The API waits for metadata; Connect waits for Kafka/API; the worker waits for
API/Connect; the web waits for API. Check `docker compose ps` and
`docker compose logs --tail 100 SERVICE` if readiness fails. Port conflicts can
be resolved with root `.env` overrides. Preserve `.env` when recreating containers.
For production use the [deployment guide](../deployment/production.md).
