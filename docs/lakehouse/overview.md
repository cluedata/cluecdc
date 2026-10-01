# Lakehouse architecture

ClueCDC currently keeps the Lakehouse path intentionally small:

- **Object storage** stores Iceberg data and metadata files on Amazon S3 or MinIO.
- **Catalog** uses the Iceberg sink's built-in Hadoop catalog and needs no separate service.
- **Table format** defines table semantics. Phase 1 supports Apache Iceberg only.

```mermaid
flowchart LR
  Postgres --> Debezium --> Kafka --> IcebergSink[Iceberg sink]
  IcebergSink --> Storage[S3 / MinIO]
```

Every external endpoint is a reusable `Connection`. Database source and
destination screens are capability-filtered views of that inventory; the legacy
tables remain runtime adapters with the same IDs during migration. A
`LakehouseTarget` references one storage connection without copying credentials.
It is composed while creating a Delivery, not
managed as a top-level connection category. `PipelineDestination` remains the
single delivery relationship, with `delivery_type=ICEBERG` for Iceberg sinks.

See [object storage](object-storage.md), [Iceberg](iceberg.md), and
[local development](local-development.md).
