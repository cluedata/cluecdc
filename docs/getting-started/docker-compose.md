# Docker Compose

The default `compose.yaml` is the smallest practical ClueCDC runtime:

| Service | Role | Default |
| --- | --- | --- |
| `metadata-db` | ClueCDC application metadata only | Runtime |
| `kafka` | Single local KRaft broker | Runtime |
| `kafka-connect` | Debezium source, JDBC sink and Aiven S3 sink connectors | Runtime |
| `cluecdc-api` | Control-plane HTTP API | Runtime |
| `cluecdc-worker` | Leased jobs, reconciliation, snapshots and alert dispatch | Runtime |
| `cluecdc-web` | Web UI and same-origin API proxy | Runtime |

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Open `http://localhost:3000`. Containers communicate over the Compose network
using service names; for example, Kafka Connect resolves secrets through
`http://cluecdc-api:8000`.

## Developer tools

CloudBeaver is optional and is not a product dependency:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d
```

It is available at `http://localhost:8978`. Configure any database connections
you need through its own UI.

## Integration and E2E fixtures

Source and destination PostgreSQL/MySQL containers and MinIO exist only in
`compose.test.yaml`:

```bash
docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait
```

The optional workload generator is behind the `tools` profile:

```bash
docker compose -f compose.yaml -f compose.test.yaml --profile tools up -d
```

These fixture credentials are test-only and live in `.env.test.example`.
They are not required to open or operate ClueCDC.

MinIO is built from the checksum-verified upstream release
`RELEASE.2025-09-07T16-13-09Z`; its public registry images are no longer available.
It remains an AGPL-3.0 test dependency, separate from the default application
runtime. A one-shot bootstrap creates `cluecdc-cdc` before the test API starts.

Run `python scripts/demo-object-storage.py` after installing `apps/api[dev]`
to verify real PostgreSQL insert, update and delete envelopes in MinIO objects.

## Storage and health

Metadata and Kafka use named persistent volumes. The API waits for metadata
PostgreSQL health; Connect waits for Kafka and API health; the web service waits
for API health. `docker compose down` preserves volumes and
`docker compose down -v` irreversibly removes local state.

The broker, Connect REST endpoint, API, and web UI bind to loopback by default.
The metadata database is not exposed to the host.

This topology is for local use. See [production considerations](../deployment/production.md)
and [scaling](../deployment/scaling.md) before deploying a shared environment.
