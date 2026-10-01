# Lakehouse troubleshooting

- **Storage authentication fails:** verify keys, region, endpoint scheme, and
  path-style addressing. MinIO normally needs path style.
- **Iceberg plugin missing:** rebuild `kafka-connect` and inspect its plugins.
- **No snapshot appears:** wait for the commit interval and inspect the delivery
  task. Confirm the control topic can be created.
- **Update/delete duplicates rows:** use UPSERT, format-version 2, stable
  identifier fields, and avoid partitioning on mutable fields.
- **Pipeline is degraded:** open the delivery and inspect component status;
  storage failures are independent from capture health.
