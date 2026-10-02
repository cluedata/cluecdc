# Installation

## Clone and bootstrap

```bash
git clone https://github.com/cluedata/cluecdc.git
cd cluecdc
cp .env.example .env
python scripts/bootstrap.py
docker compose up -d --build --wait --wait-timeout 240
```

`bootstrap.py` replaces documented development credentials with unique local values. Keep `.env` private. Existing database volumes retain their original passwords, so changing `.env` is not a password-rotation procedure.

Open the UI at <http://localhost:3000> and the API documentation at <http://localhost:8000/docs>.

## Confirm readiness

```bash
docker compose ps
curl http://localhost:8000/health/ready
curl http://localhost:3000
```

All default services should be healthy. If a service is not ready, inspect `docker compose logs --tail 100 SERVICE` before retrying.

## Optional developer tools and test fixtures

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d
docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait
```

The developer override adds optional database inspection tooling. The test
override adds PostgreSQL/MySQL source and destination fixtures for integration
and E2E checks. Neither is required for the default runtime.
