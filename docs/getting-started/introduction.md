# Introduction

ClueCDC is a self-hosted management plane for Change Data Capture. It turns database, Debezium, Kafka, and Kafka Connect configuration into explicit resources and lifecycle operations while leaving row movement to those data-plane systems.

Use ClueCDC when you want repeatable connector configuration, table discovery, controlled pipeline changes, destination fan-out, and one operational view without placing a proprietary relay in the data path.

## Community scope

The current release implements PostgreSQL and MySQL capture through Debezium
source connectors and PostgreSQL/MySQL delivery through JDBC sink connectors. It
includes a Next.js interface, a FastAPI API, PostgreSQL metadata, Alembic
migrations, a reconciliation worker, structured logs, application metrics,
audit records, and notification channels.

ClueCDC includes local email/password authentication, invitation-based user
provisioning, and role-based access control. It does not bundle external identity
providers, managed Kafka, automatic TLS/ACL provisioning, schema registry, or a
historical metrics backend. The compatibility developer mode is local/test-only.

## Where to go next

- Use [Prerequisites](prerequisites.md) and [Installation](installation.md) to start the stack.
- Follow [First CDC Pipeline](first-pipeline.md) for PostgreSQL-to-PostgreSQL synchronization.
- Read [Architecture](../architecture/overview.md) before a production design.
