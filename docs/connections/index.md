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

## Test before saving

The PostgreSQL/MySQL Source forms, unified PostgreSQL/MySQL/AWS S3/MinIO
Connection forms, database Destination dialogs, and Kafka/Kafka Connect cluster
forms provide a **Test connection** button. You can test directly from the
connection-details form without creating a resource first.

Tests use the current form values. In an edit form, blank credential fields reuse
the encrypted saved credentials; entered values are used only for that test until
you save. Draft tests do not save configuration, rotate credentials, create a
connection, or update the saved connection's health status. Changing inputs clears
the displayed result; a response for an older draft is not shown as a current pass.

The button shows progress and allows retry after failure. Database permission
failures returned with HTTP 200 are still displayed as failed checks. Object-storage
tests create and remove a uniquely named probe object to verify bucket access.
Connection success alone does not prove CDC readiness: run discovery and the
provider-specific readiness check before creating a capture pipeline.
