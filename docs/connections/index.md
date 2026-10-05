# Connections

ClueCDC supports PostgreSQL and MySQL as sources and destinations, and AWS S3
and MinIO as object-storage destinations. Kafka and Kafka Connect clusters are
registered infrastructure resources. [Object storage](../connectors/object-storage.md)
uses the Aiven S3 sink and JSONL files.

`Connection` is the only persisted endpoint model. Sources and destinations are
capability-filtered views of that resource; pipelines and deliveries refer to
the same connection UUID. Credentials are write-only: public APIs return masked
credential status. Kafka Connect receives references resolved through a private
machine-authenticated API; plaintext credentials remain encrypted in metadata.

Test every connection from the ClueCDC runtime network. `localhost` inside a container means that container, not the host.
