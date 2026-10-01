# PostgreSQL to PostgreSQL example

This example uses the actual services in the root Compose file:

```text
cdc-source-postgres -> Debezium -> Kafka -> JDBC sink -> destination-postgres
```

Start the stack and run the reproducible acceptance scripts:

```bash
cp .env.example .env
docker compose up -d --build --wait
python scripts/demo.py --api-url http://localhost:3000/api/v1
python scripts/demo-destination.py --skip-capture-demo
```

The source fixtures are `customers` and `orders`. The script verifies real
insert, update, delete, pause/catch-up, and restart behavior and writes only
sanitized results under ignored `artifacts/`. Full manual steps and credentials
by environment-variable name are in the [user guide](../../user_guide.md).
