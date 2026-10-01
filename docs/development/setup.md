# Development setup

Install Node.js 24 and Python 3.12, then:

```bash
npm ci
python -m pip install -e "apps/api[dev]"
cp .env.example .env
python scripts/bootstrap.py
```

The simplest full environment is `docker compose up -d --build --wait`. For
frontend iteration, leave the API stack running and use `npm run dev`; set
`API_INTERNAL_URL` when the API is not at `http://localhost:8000`.

Run backend commands from the repository root:

```bash
python -m ruff check apps/api
python -m ruff format --check apps/api
python -m mypy apps/api/app
python -m pytest apps/api/tests -q
```

Run frontend commands with `npm run lint`, `npm run typecheck`, `npm run test:web`,
and `npm run build`. On Windows PowerShell with script execution disabled, use
`npm.cmd`.

From `apps/api`, apply migrations with `alembic upgrade head`, inspect pending
schema drift with `alembic check`, and roll back one version with
`alembic downgrade -1`. Never use runtime `create_all` in the application.
