# Docker Compose

The default `compose.yaml` is the smallest practical ClueCDC runtime:

It uses Linux containers on x86-64 (`amd64`) or ARM64, including Apple Silicon.
See [prerequisites](prerequisites.md) for Docker Desktop/WSL 2 requirements,
memory, disk space, and supported hosts. The same file is used on macOS,
Ubuntu/Linux, and Windows; Docker builds for its engine's native CPU.

| Service          | Role                                                      | Default |
| ---------------- | --------------------------------------------------------- | ------- |
| `metadata-db`    | ClueCDC application metadata only                         | Runtime |
| `kafka`          | Single local KRaft broker                                 | Runtime |
| `kafka-connect`  | Debezium source, JDBC sink and Aiven S3 sink connectors   | Runtime |
| `cluecdc-api`    | Control-plane HTTP API                                    | Runtime |
| `cluecdc-worker` | Leased jobs, reconciliation, snapshots and alert dispatch | Runtime |
| `cluecdc-web`    | Web UI and same-origin API proxy                          | Runtime |

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose ps
```

Open `http://localhost:3000`. Containers communicate over the Compose network
using service names; for example, Kafka Connect resolves secrets through
`http://cluecdc-api:8000`.

Sign in to the loopback-only development stack with the public demo account:

```text
Email:    admin@cluecdc.local
Password: cluecdc-admin
```

The copied environment also contains public demo passwords and cryptographic
keys; the quick start does not generate unique secrets. These values and the
demo account are not suitable for a shared or production deployment. See
[configuration](../operations/configuration.md#optional-unique-local-secrets)
for optional local secret generation before first startup.

## Build and host compatibility

Validate and build without starting containers (works in Bash and PowerShell):

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d --wait --wait-timeout 300
```

For Windows PowerShell, initialize with `Copy-Item .env.example .env`.
Host Python and Node.js are not required; application dependencies are
installed inside the Docker build stages.

The API and web install their native dependencies inside their Linux build
stages. Host `node_modules`, Python virtual environments, and generated caches
are excluded from the build context. Connect compiles portable Java bytecode
on the builder's CPU, and the optional MinIO fixture cross-compiles its Go binary
for the target CPU. Do not set a global `DOCKER_DEFAULT_PLATFORM` unless you
intentionally want emulation; it can make Apple Silicon builds much slower.

For relevant changes, CI renders core/test configurations and CI overrides on
Ubuntu, macOS, and Windows using Compose v2.24.7 (the supported minimum-version
check). Native Ubuntu runners build cached runtime images and run core smoke
checks. Ordinary application PRs use AMD64; packaging/dependency/connector/workload
PRs also use ARM64. Relevant `main`/`master` pushes and full manual CI runs check
both architectures without emulation. CDC/S3/full-browser acceptance is selected
independently on AMD64. Menu/login/CSS changes use core-only browser checks,
without integration databases or MinIO. Connect/MinIO final image caches are
architecture- and upstream-digest-specific. Documentation-only changes skip
those containers. See the
[CI selection policy](../development/testing.md#github-actions).
The release workflow builds both architectures into one image index. These
Linux checks do not replace a Docker Desktop runtime check on a specific macOS
or Windows installation.

There is no separate developer Compose override. Use your preferred external
database client with the test fixture ports when database inspection is needed.

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

### Fixture connections and credentials

Both engines use the same database and application-account names by role:

| Fixture                | Host connection  | Database              | User               | Password         |
| ---------------------- | ---------------- | --------------------- | ------------------ | ---------------- |
| PostgreSQL source      | `localhost:5434` | `cluecdc_source`      | `source_user`      | `source123`      |
| MySQL source           | `localhost:3307` | `cluecdc_source`      | `source_user`      | `source123`      |
| PostgreSQL destination | `localhost:5435` | `cluecdc_destination` | `destination_user` | `destination123` |
| MySQL destination      | `localhost:3308` | `cluecdc_destination` | `destination_user` | `destination123` |

When registering connections in ClueCDC, use `cdc-source-postgres:5432`,
`cdc-source-mysql:3306`, `destination-postgres:5432`, or `destination-mysql:3306`
instead of the host addresses. PostgreSQL uses the `public` schema; its destination
also provides an `analytics` schema for fan-out tests. MySQL's schema name is its
database name. PostgreSQL administrators use `postgres` / `postgres123`; MySQL
administrators use `root` / `mysql123`. MinIO uses `minioadmin` / `minioadmin`,
endpoint `http://localhost:9000` (`http://minio:9000` inside Compose), and bucket
`cluecdc-cdc`. All published fixture ports bind to loopback only.

Override passwords through the existing variables in `.env.test.example`, using
your `.env` or shell environment. These public defaults are for local tests only;
the core metadata database and application authentication are unchanged.
Demo/acceptance scripts and browser tests also load `.env.test.example` as
defaults, then apply your `.env`/explicit env files and shell overrides.

### Initialization and existing volumes

All PostgreSQL/MySQL initialization and the MinIO bucket bootstrap are inline
Compose `configs` in `compose.test.yaml`; no init scripts are mounted from
`infrastructure`. Compose v2.24.7 or newer is required. SQL and shell scripts are
mounted read-only into the database images' standard init directory.

Database init runs **only on an empty data volume**. Updating names/passwords or
recreating a container does not rename existing databases, change existing users,
or rerun seeding. Existing `.env` values also override the new defaults. Do not
use `docker compose down -v` on an existing stack to apply this change: that would
also delete metadata and Kafka data. Back up any needed data, then explicitly
migrate the fixture databases/accounts or recreate only the four fixture data
volumes. Update saved source/destination connections to match afterwards.

For a completely fresh test environment without altering existing volumes, use
a separate Compose project and override the published ports to unused values.

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
