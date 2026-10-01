# Open-source audit

The publication workspace was produced from the current working tree without copying `.git`, `.env`, dependencies, caches, build outputs, test artifacts, or volumes. A fresh `main` repository avoids publishing personal commit metadata and any unreviewed historical objects.

Implemented code was verified for PostgreSQL/MySQL capture and JDBC delivery, S3/MinIO object storage, Iceberg delivery, lifecycle operations, alerts, audit, structured logging, metrics, developer/token authentication, and Alembic migrations. SQL Server and Trino are explicitly not supported. Historical metrics are unavailable without a provider.

Apache License 2.0 is technically compatible with the repository's permissively licensed dependency model, but maintainers must confirm ownership and contributor permission for every included file before publication. Font license texts and a NOTICE file are retained. This audit is engineering evidence, not legal advice.

See `OPEN_SOURCE_READINESS.md` in the repository root for validation evidence and remaining publication actions.
