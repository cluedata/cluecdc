# Docker deployment assets

The core local topology is `compose.yaml`; integration and E2E fixtures are in
`compose.test.yaml`. There is no separate developer Compose override.
Dockerfiles and initialization assets remain under `infrastructure/`.

Copy `.env.example` to `.env`, then run `docker compose up -d --build --wait`.
This loopback-only demo uses the public example secrets unchanged and requires
no host Python or Node.js installation. See the
[configuration guide](../../docs/operations/configuration.md#optional-unique-local-secrets)
for optional unique local secrets before the first startup.
See the documentation site deployment section for service scope and limits.

Both files use Linux containers and the Docker engine's native `amd64` or
`arm64` architecture. Use Docker Engine on Ubuntu/Linux or Docker Desktop on
macOS/Windows (WSL 2 with Linux containers). See the
[Compose guide](../../docs/getting-started/docker-compose.md) for prerequisites
and platform-specific commands.
