# First CDC pipeline

This tutorial uses the PostgreSQL source and destination fixtures from the test
Compose override. Start them first:

```bash
docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait
```

This exercises the real Debezium, Kafka, and JDBC sink path.

Sign in as Admin for infrastructure and connection setup. Once those reusable
resources exist, an Ops user can create and operate pipelines and deliveries.

## 1. Register infrastructure

Open **Infrastructure** in ClueCDC and register:

| Resource | Value |
| --- | --- |
| Kafka bootstrap servers | `kafka:29092` |
| Kafka Connect URL | `http://kafka-connect:8083` |

These are container-network addresses. A host client would use `localhost:9092` and `localhost:8083`.

## 2. Register the PostgreSQL source

Open **Connections → Sources → Create Source** and use:

| Field | Value |
| --- | --- |
| Host / port | `cdc-source-postgres` / `5432` |
| Database | `commerce` |
| User | `cdc_user` |
| Password | `cluecdc-source-test-only` unless overridden |

Test the connection, save it, run discovery, and review CDC readiness. Select `public.customers` and `public.orders`; both have stable keys and local logical-replication configuration.

## 3. Register the destination

Open **Connections → Destinations → Create Destination** and register PostgreSQL:

| Field | Value |
| --- | --- |
| Host / port | `destination-postgres` / `5432` |
| Database | `analytics` |
| User | `delivery_user` |
| Password | `cluecdc-destination-test-only` unless overridden |

Test and save the destination. Registration stores a reusable endpoint; it does
not start a sink connector or move data.

## 4. Create the pipeline and first delivery

Open **Data Flow → Pipelines → Create Pipeline**. The five-step wizard performs
the complete initial path:

1. Select the PostgreSQL source.
2. Select `public.customers` and `public.orders`, snapshot mode `initial`, and a
   unique topic prefix such as `commerce`.
3. Select the registered Kafka and Kafka Connect clusters.
4. Select the PostgreSQL destination, name the delivery, and map each topic to
   its destination table.
5. Review and create.

The UI previews and saves the capture, deploys its Debezium connector, prepares
topics, validates the source keys/types, destination permissions, installed
JDBC plugin, and mappings, then deploys the delivery. If delivery creation
fails after capture succeeds, the wizard reports the partial result and offers
a delivery retry. Wait until both connector tasks report **RUNNING**.

## 5. Verify synchronization

Insert a source record:

```bash
docker compose -f compose.yaml -f compose.test.yaml exec -T cdc-source-postgres \
  psql -U postgres -d commerce -c \
  "INSERT INTO public.customers (email, name) VALUES ('ada-new@example.test', 'Ada Lovelace');"
```

Inspect topic metadata and connector state, then query the destination directly:

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
3. Expected Kafka topic existence and partition offset movement.
4. Delivery connector/task state and sanitized task trace.
5. Target table keys, types, and permissions.

See [Pipeline synchronization](../troubleshooting/pipeline-sync.md) for corrective actions.
