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

## Optional lakehouse profile

```bash
docker compose --profile lakehouse up -d --build --wait
```

This adds MinIO for local S3-compatible Iceberg storage. It does not add Trino or a separate catalog; the implemented Iceberg path uses the connector's Hadoop catalog.
