# Quick start

## Requirements

- Git and Python 3.12+ for bootstrap
- Docker Engine/Desktop with Linux containers and Compose v2.24+ or v5
- Approximately 6 GB of Docker memory

See [platform prerequisites](prerequisites.md) for Ubuntu/Linux, macOS
(Intel/Apple Silicon), and Windows (Docker Desktop with WSL 2).

```bash
git clone https://github.com/cluedata/cluecdc.git
cd cluecdc
python scripts/bootstrap.py
docker compose up -d --build --wait
```

Open `http://localhost:3000/login` and sign in with the development-only account:

```text
Email:    admin@cluecdc.local
Password: cluecdc-admin
```

Use `py -3` or `python3` if required by your host. Bootstrap generates independent
technical secrets and a shared local database password. Node.js runs in the image.
Keep `.env` private. Never expose the development account or reuse its metadata
in production. See [configuration](../operations/configuration.md).

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
