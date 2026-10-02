# Docker deployment assets

The core local topology is `compose.yaml`. Optional developer tooling is in
`compose.dev.yaml`; integration and E2E fixtures are in `compose.test.yaml`.
Dockerfiles and initialization assets remain under `infrastructure/`.

Run `python scripts/bootstrap.py` before `docker compose up -d --build --wait`.
See the documentation site deployment section for service scope and limits.
