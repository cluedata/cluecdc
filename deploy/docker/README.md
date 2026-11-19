# Docker deployment assets

The core local topology is `docker-compose.yml`; integration and E2E fixtures are in
`docker-compose.demo.yml`. There is no separate developer Compose override.
Dockerfiles and initialization assets remain under `infrastructure/`.

Run `python scripts/bootstrap.py`, then `docker compose up -d --build --wait`.
Bootstrap requires Python and generates independent technical secrets plus one
local database password. Node.js runs inside the image. See the
[configuration guide](../../docs/operations/configuration.md#local-credentials)
for credentials and safe upgrades.
See the documentation site deployment section for service scope and limits.

Both files use Linux containers and the Docker engine's native `amd64` or
`arm64` architecture. Use Docker Engine on Ubuntu/Linux or Docker Desktop on
macOS/Windows (WSL 2 with Linux containers). See the
[Compose guide](../../docs/getting-started/docker-compose.md) for prerequisites
and platform-specific commands.
