# Docker deployment assets

The supported development topology remains at the repository root as
`docker-compose.yml`; Dockerfiles and initialization assets remain under
`infrastructure/` to preserve existing build contexts and scripts.

Run `python scripts/bootstrap.py` before `docker compose up -d --build --wait`.
See the documentation site deployment section for service scope and limits.
