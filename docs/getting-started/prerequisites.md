# Prerequisites

## Docker Compose path

- Git
- Docker Engine or Docker Desktop with Compose v2
- Approximately 6 GB free Docker memory
- Ports 3000, 8000, 8083, 9092, 5433-5435, 3307-3308, and 8978 available, or overridden in `.env`

The application images are built locally. Docker downloads PostgreSQL, MySQL, Kafka, Debezium Connect, and CloudBeaver images on first use.

## Host development path

- Node.js 24 and npm
- Python 3.12
- Docker Compose for metadata and data-plane dependencies

Windows PowerShell users can replace `cp` with `Copy-Item`. Commands shown inside containers use the Linux shell supplied by the image.

## Source database access

An external PostgreSQL source needs logical WAL, replication capacity, a stable primary key, `SELECT`, replication privileges, and ownership compatible with filtered publication creation. MySQL needs row-based binlogs with full row images and the documented replication grants. ClueCDC reports readiness problems; it does not silently alter production servers.
