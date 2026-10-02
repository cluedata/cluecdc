# Ecommerce CDC test container

`commerce-generator` contains Python scripts that connect to the existing
PostgreSQL ecommerce source: database `commerce` on `cdc-source-postgres`, using
the table-owning `cdc_user` role and `SOURCE_PASSWORD` from your private `.env`.
It uses the existing `customers`, `orders` and `payments` tables. No new database
or source registration is required.

## Start the tools container

```powershell
docker compose -f compose.yaml -f compose.test.yaml --profile tools up -d --build commerce-generator
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python generate.py status
```

The service is in the optional `tools` profile, so a normal stack startup does
not start it. Explicitly targeting the service starts it without needing a
profile flag. Its default process is idle: starting the container does not
insert rows or change schemas. Scripts produce one JSON result per committed
batch, including the run ID, affected counts and first inserted IDs.

## Insert ecommerce transactions

```powershell
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python insert.py --count 10 --run-id checkout-demo
```

Each group inserts one customer, one order linked to that customer, and one
payment linked to that order. This example inserts 30 rows in one committed
transaction. Orders use decimal totals, timestamps and a pending status.
Emails have the form `cdc-load-checkout-demo.<unique-token>@example.test`, so
repeated inserts are unique. The count is limited to 1-10000 groups per batch.

## Update generated rows

```powershell
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python update.py --count 10 --run-id checkout-demo
```

Only fixtures with that exact run ID are selected. Names, timestamps, order
statuses and totals, and payment statuses and amounts change together.
Subsequent updates alternate states. Omit `--run-id` to update any rows generated
by these scripts. Existing application rows without the generated email prefix
are excluded. An empty selection reports zero affected rows.

## Generate traffic for a bounded duration

```powershell
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python simulate.py --duration 120 --interval 1 --batch-size 5 --run-id checkout-load
```

Each batch commits INSERTs, then UPDATEs in a separate transaction. The default
duration is 60 seconds, interval is one second, and batch size is five customer
groups. Actual rates depend on transaction time; the interval is a delay between
batches, not a throughput guarantee. The workload does not alter schemas or
delete rows. Ctrl+C stops after the current transaction; SIGTERM also stops the
loop. Generated rows are retained for inspection.

## Test schema changes

```powershell
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python alter.py --table customers --column cdc_test_note --type text --default "checkout-test"
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python insert.py --count 1 --run-id after-schema
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python update.py --count 1 --run-id after-schema
```

`alter.py` modifies only column names starting with `cdc_test_`; the existing
application columns are excluded. Supported types are `text`, `bigint`,
`numeric` (`numeric(12,2)`), `timestamptz` and `boolean`. Adding an existing test
column reports `changed: false` and leaves its definition unchanged.

Change the default for future inserts, using the column's type:

```powershell
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python alter.py --table customers --column cdc_test_note --action set-default --type text --default "revised-test"
```

Explicitly remove that test column when finished:

```powershell
docker compose -f compose.yaml -f compose.test.yaml exec commerce-generator python alter.py --table customers --column cdc_test_note --action drop
```

Dropping a test column removes its values. No drop operation runs automatically.
These scripts do not change primary keys, replica identities, publications,
replication slots, pipeline definitions or delivery mappings.

## Observe changes in ClueCDC

1. Ensure the source pipeline is running and captures the tables being changed.
   The existing demo typically captures `public.customers` and `public.orders`;
   include `public.payments` in a pipeline to inspect payment events too.
2. Open **Events**, choose the corresponding Kafka topic, and fetch recent
   events. Filter by the inserted IDs or inspect the generated customer email.
   With the standard `commerce` topic prefix, topics are
   `commerce.public.customers`, `commerce.public.orders` and
   `commerce.public.payments`.
3. After an ALTER, run **Discover tables** on the source. Check **Schemas** for
   the new version and diff. ClueCDC detects schema changes during discovery;
   PostgreSQL DDL itself is not an INSERT/UPDATE event.
4. To test downstream evolution, refresh discovery and validate/update delivery
   mappings to include new columns. The source ALTER does not automatically
   update the sink's typed mapping. Whether target columns are created depends
   on the delivery's evolution options.

## One-off containers and another source

The same commands can run without a persistent tools container:

```powershell
docker compose -f compose.yaml -f compose.test.yaml run --rm commerce-generator python insert.py --count 3
docker compose -f compose.yaml -f compose.test.yaml run --rm commerce-generator python update.py --count 3
docker compose -f compose.yaml -f compose.test.yaml run --rm commerce-generator python alter.py --help
```

Compose accepts `WORKLOAD_PGHOST`, `WORKLOAD_PGPORT`, `WORKLOAD_PGDATABASE`,
`WORKLOAD_PGUSER` and `WORKLOAD_PGSCHEMA` overrides in the private `.env`. For a
different password, pass an environment variable to the one-off container
without placing its value on the command line:

```powershell
docker compose -f compose.yaml -f compose.test.yaml run --rm -e PGPASSWORD commerce-generator python generate.py status
```

This forwards `PGPASSWORD` from your shell. The selected database/schema must
already contain compatible ecommerce tables; credentials must allow writes and,
for ALTER, ownership of the selected table. The scripts do not initialize or
reset another source.

## Verification and stop

```powershell
docker compose -f compose.yaml -f compose.test.yaml run --rm commerce-generator python -m unittest -v
docker compose -f compose.yaml -f compose.test.yaml stop commerce-generator
```

Regression checks use real PostgreSQL, create an isolated test schema inside a
transaction and roll it back. They check foreign-key-linked inserts, update
isolation, unchanged application rows, synchronized totals, repeated updates,
quoted defaults, idempotent DDL and invalid arguments.
