# Local Lakehouse development

Start the optional profile:

```bash
python scripts/bootstrap.py
docker compose --profile lakehouse up -d --build --wait
```

| Layer | Container address   | Development configuration                 |
| ----- | ------------------- | ----------------------------------------- |
| MinIO | `http://minio:9000` | bucket `lakehouse`, base path `warehouse` |

Create a MinIO connection using the values in `.env`, then create a Lakehouse
Delivery with warehouse `s3://lakehouse/warehouse`
and namespace `production`. From the host, substitute `localhost` and published
ports.

Load the source fixture and verify the delivery task plus the generated Iceberg
data and metadata objects in MinIO:

```bash
docker compose exec -T cdc-source-postgres psql -U postgres -d commerce \
  < examples/postgres-to-iceberg/customers.sql
```

The example README covers update, add-column, and delete verification.
