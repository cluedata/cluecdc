# Quick start

## Requirements

- Git
- Docker Engine/Desktop with Compose v2
- Approximately 6 GB of Docker memory

```bash
git clone https://github.com/cluedata/cluecdc.git
cd cluecdc
cp .env.example .env
python scripts/bootstrap.py
docker compose up -d --build --wait
```

Open `http://localhost:3000/login` and sign in with the development-only account:

```text
Email:    admin@cluecdc.local
Password: cluecdc-admin
```

The example environment is loopback-only. `bootstrap.py` generates unique
infrastructure, encryption, and session secrets while deliberately preserving
that public local login. It does not overwrite values you already customized.
Never expose the demo account or carry its metadata database into production.

A new installation contains no registered ClueCDC sources, clusters,
pipelines, or destinations and does not fabricate metrics. As Admin, register
`kafka:29092` and `http://kafka-connect:8083` in **Infrastructure** to use the
bundled data plane.

The default Compose stack has six services: metadata PostgreSQL, Kafka, Kafka
Connect, API, worker, and web. Confirm that they are healthy:

```bash
docker compose ps
curl http://localhost:8000/health/ready
```

Changing `.env` later does not rotate passwords stored in initialized database
volumes. Follow [First CDC Pipeline](first-pipeline.md) for the real PostgreSQL
capture and destination flow. For production, disable the default Admin and
follow [Authentication](../security/authentication.md).
