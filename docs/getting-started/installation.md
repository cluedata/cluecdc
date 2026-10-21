# Installation

## Clone and start

Check the [platform prerequisites](prerequisites.md) first. Use Linux containers
on Docker Desktop for macOS/Windows or Docker Engine on Ubuntu/Linux.

```bash
git clone https://github.com/cluedata/cluecdc.git
cd cluecdc
cp .env.example .env
docker compose up -d --build --wait --wait-timeout 240
```

On Windows PowerShell, use `Copy-Item .env.example .env`. The Docker commands
are identical on all hosts; no host Python or Node.js installation is required.

The copied example includes public demo passwords and cryptographic keys,
suitable only for the loopback development stack. The commands above do not
generate unique secrets. See
[optional unique local secrets](../operations/configuration.md#optional-unique-local-secrets)
before the first startup if desired. Keep `.env` private after customizing it.
Existing database volumes retain their original passwords, so changing `.env`
is not a password-rotation procedure.

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

## Optional test fixtures

```bash
docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait
```

The test override adds PostgreSQL/MySQL source and destination fixtures and
MinIO for integration and E2E checks. It is not required for the default runtime.
