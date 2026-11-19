# Installation

## Clone and start

Check the [platform prerequisites](prerequisites.md) first. Use Linux containers
on Docker Desktop for macOS/Windows or Docker Engine on Ubuntu/Linux.

```bash
git clone https://github.com/cluedata/cluecdc.git
cd cluecdc
python scripts/bootstrap.py
docker compose up -d --build --wait --wait-timeout 240
```

Bootstrap requires Python 3.12+ (`py -3` or `python3` on some hosts); no host
Node.js is needed. It generates independent technical secrets and one local
database password without manual editing. Keep `.env` private and backed up.
Existing installations must retain their credentials and volumes; read the
[upgrade instructions](../operations/configuration.md#existing-installations).

Open the UI at <http://localhost:3000/login> and sign in with
`admin@cluecdc.local` / `cluecdc-admin`. This account is only for the
loopback development stack. The API documentation is at
<http://localhost:8000/docs>.

## Confirm readiness

```bash
docker compose ps
curl http://localhost:8000/health/ready
curl http://localhost:3000
```

All default services should be healthy. If a service is not ready, inspect `docker compose logs --tail 100 SERVICE` before retrying.

## Demo and tests

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build --wait
```

The demo extension adds PostgreSQL/MySQL source and destination fixtures.
MinIO is available through the `object-storage` profile. Integration/E2E tests
reuse this topology; see [Compose](docker-compose.md) and
[testing](../development/testing.md) for isolated commands.
