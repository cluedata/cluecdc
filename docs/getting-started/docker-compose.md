# Docker Compose

The default Compose file is the reproducible Community development/demo stack.
It contains Web, API, metadata PostgreSQL, Kafka, Kafka Connect, PostgreSQL and
MySQL source/destination databases, and CloudBeaver. `commerce-generator` is
behind the `tools` profile and does not start by default. MinIO is behind the
optional `lakehouse` profile:

```bash
docker compose --profile lakehouse up -d --build --wait
```

All host publications bind to `127.0.0.1`. Container-to-container configuration
must use Compose DNS names and container ports, not host ports.

| Service                | Container address           | Default host address |
| ---------------------- | --------------------------- | -------------------- |
| Web                    | `cluecdc-web:3000`          | `localhost:3000`     |
| API                    | `cluecdc-api:8000`          | `localhost:8000`     |
| Kafka                  | `kafka:29092`               | `localhost:9092`     |
| Kafka Connect          | `kafka-connect:8083`        | `localhost:8083`     |
| PostgreSQL source      | `cdc-source-postgres:5432`  | `localhost:5434`     |
| PostgreSQL destination | `destination-postgres:5432` | `localhost:5435`     |

```bash
docker compose up -d --build --wait
docker compose ps
docker compose logs --tail 100 cluecdc-api kafka-connect
docker compose down
```

Use `docker compose down` for a recoverable stop. `docker compose down -v`
permanently deletes all named volumes and is intentionally not part of normal
cleanup instructions.

For an end-to-end acceptance run:

```bash
python scripts/demo.py --api-url http://localhost:3000/api/v1
python scripts/demo-destination.py --skip-capture-demo
python scripts/verify-secrets.py
```
