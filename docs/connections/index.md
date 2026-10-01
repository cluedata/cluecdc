# Connections

ClueCDC currently supports PostgreSQL and MySQL databases as sources and destinations, Kafka clusters, Kafka Connect clusters, and Amazon S3 or MinIO object storage. Apache Iceberg is configured as a lakehouse target over object storage.

Credentials are write-only: the API encrypts database and object-store secrets in metadata and returns references, never plaintext. Kafka Connect resolves a narrowly scoped secret reference through the internal API at runtime.

Test every connection from the ClueCDC runtime network. `localhost` inside a container means that container, not the host.
