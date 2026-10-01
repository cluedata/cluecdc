# Quick start

## Requirements

- Git
- Docker Engine/Desktop with Compose v2
- Approximately 6 GB of Docker memory

```bash
git clone https://github.com/cluedata/cluecdc.git
cd cluecdc
cp .env.example .env
docker compose up -d --build --wait
```

Open `http://localhost:3000`. A new installation intentionally contains no
registered ClueCDC sources, clusters, pipelines, destinations, or synthetic
metrics. Register `kafka:29092` and `http://kafka-connect:8083` from the UI to
use the bundled data plane.

The example environment is loopback-only and immediately runnable. Generate
unique local values before retaining data:

```bash
python scripts/bootstrap.py
docker compose up -d --build
```

Changing `.env` later does not rotate passwords stored in initialized database
volumes. Follow [First CDC Pipeline](first-pipeline.md) for the real PostgreSQL
capture and destination flow.
