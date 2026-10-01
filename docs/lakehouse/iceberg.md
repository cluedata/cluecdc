# Apache Iceberg delivery

An Iceberg `LakehouseTarget` combines an Amazon S3 or MinIO storage connection,
warehouse location, namespace, file format, CDC write semantics, identifier
fields, partition fields, automatic table creation, and schema evolution.
The connector uses its built-in Hadoop catalog, so no separate catalog or query
engine connection is required.

Deployment uses the existing Kafka Connect cluster and creates an Apache Iceberg
sink. The generated configuration uses the connector's Debezium transform,
static multi-table routing, format-version 2, per-table identifier columns, and
equality-delete CDC settings for `UPSERT`. Inserts, snapshots, updates, and
deletes are derived from the Debezium envelope; deletes are not silently ignored.

`APPEND_ONLY` intentionally disables row mutation and therefore cannot be paired
with delete propagation. `UPSERT` requires a source primary key or explicit
identifier fields. Partitioning an upsert table by mutable/non-identifier values
can create surprising equality-delete behavior and should be reviewed carefully.

Targets are created in the Delivery flow and may reuse the same storage
connection across multiple deliveries. Automatic
evolution adds compatible fields through the connector. Unsupported
source types fail deployment with `ICEBERG_TYPE_UNSUPPORTED`; unbounded decimals
require an explicit precision and scale. Destructive schema changes are not
automatically applied.
