# First CDC pipeline

This tutorial uses the PostgreSQL source and destination fixtures from the test
Compose override. Start them first:

```bash
docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait
```

This exercises the real Debezium, Kafka, and JDBC sink path.

## 1. Register infrastructure

Open **Infrastructure** in ClueCDC and register:

| Resource | Value |
| --- | --- |
| Kafka bootstrap servers | `kafka:29092` |
| Kafka Connect URL | `http://kafka-connect:8083` |

These are container-network addresses. A host client would use `localhost:9092` and `localhost:8083`.

## 2. Register the PostgreSQL source

Open **Data Movement → Sources → Add source** and use:

| Field | Value |
| --- | --- |
| Host / port | `cdc-source-postgres` / `5432` |
| Database | `commerce` |
| User | `cdc_user` |
| Password | `cluecdc-source-test-only` unless overridden |

Test the connection, save it, run discovery, and review CDC readiness. Select `public.customers` and `public.orders`; both have stable keys and local logical-replication configuration.

## 3. Create capture

Open **Pipelines → Create pipeline**. Select the source, registered Kafka and Connect clusters, the two tables, snapshot mode `initial`, and a unique topic prefix such as `commerce`. Validate, create, and deploy. The connector and its task should reach **RUNNING**.

## 4. Add the destination

From the pipeline's **Destinations** tab choose **Add destination**, then create a PostgreSQL destination:

| Field | Value |
| --- | --- |
| Host / port | `destination-postgres` / `5432` |
| Database | `analytics` |
| User | `delivery_user` |
| Password | `cluecdc-destination-test-only` unless overridden |

Map the customers and orders topics to destination tables. Validate the source keys/types, destination permissions, topics, and installed JDBC plugin; then deploy.

## 5. Verify synchronization

Insert a source record:

```bash
docker compose -f compose.yaml -f compose.test.yaml exec -T cdc-source-postgres \
  psql -U postgres -d commerce -c \
  "INSERT INTO public.customers (email, name) VALUES ('ada-new@example.test', 'Ada Lovelace');"
```

Inspect the pipeline **Events** tab, then query the destination:

```bash
docker compose -f compose.yaml -f compose.test.yaml exec -T destination-postgres \
  psql -U postgres -d analytics -c \
  "SELECT * FROM public.customers WHERE email = 'ada-new@example.test';"
```

The row should appear after the source and sink tasks process it. Updates and deletes follow the same path.

## 6. Add another table

Open the pipeline **Tables** tab, choose **Add table**, and select `public.payments`. ClueCDC updates the filtered publication and connector table list and requests an incremental snapshot through Debezium signaling. Watch the resulting operation until it completes.

## Diagnose a failure

Check, in order:

1. Source readiness and discovery status.
2. Capture connector and task state.
3. Expected Kafka topic existence and recent records.
4. Delivery connector/task state and sanitized task trace.
5. Target table keys, types, and permissions.

See [Pipeline synchronization](../troubleshooting/pipeline-sync.md) for corrective actions.
