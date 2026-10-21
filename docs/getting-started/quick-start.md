# Quick start

## Requirements

- Git
- Docker Engine/Desktop with Linux containers and Compose v2.24+ or v5
- Approximately 6 GB of Docker memory

See [platform prerequisites](prerequisites.md) for Ubuntu/Linux, macOS
(Intel/Apple Silicon), and Windows (Docker Desktop with WSL 2).

```bash
git clone https://github.com/cluedata/cluecdc.git
cd cluecdc
cp .env.example .env
docker compose up -d --build --wait
```

Open `http://localhost:3000/login` and sign in with the development-only account:

```text
Email:    admin@cluecdc.local
Password: cluecdc-admin
```

On Windows PowerShell, use `Copy-Item .env.example .env` instead of `cp`.
Host Python and Node.js are not required for this Compose setup.

The example environment is loopback-only and uses public demo passwords and
cryptographic keys unchanged; no unique secrets are generated automatically.
Never expose the demo account or carry its metadata database into production.
See [optional unique local secrets](../operations/configuration.md#optional-unique-local-secrets)
if you want to replace the examples before the first startup.

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
