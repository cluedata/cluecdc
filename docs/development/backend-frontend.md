# Backend and frontend development

## Backend

```bash
python -m pip install -e "apps/api[dev]"
cd apps/api
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Required settings come from the repository `.env`. Keep migrations explicit and reversible; application startup does not call `create_all`.

## Frontend

```bash
npm ci
npm run dev -w @cluecdc/web
```

The server-side proxy reads `CLUECDC_API_URL` and keeps the backend origin private. Business rules and connector configuration belong in the API, not browser components.

Run formatting, lint, type checks, tests, and builds before opening a pull request.
