# Testing

## Fast checks

```bash
npm ci
python -m pip install -e "apps/api[dev]"
npm run format:check
npm run lint
npm run typecheck
npm run test:web
npm run lint:api
npm run typecheck:api
npm run test:api
npm run build
python scripts/check-repository.py
```

API unit tests use temporary SQLite and mocked external clients where isolation is intentional. Web unit tests use Vitest and Testing Library.

## Live integration

Start the integration stack with
`docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait`, run
`scripts/demo.py` and `scripts/demo-destination.py`, then run Playwright. These
checks use the real source, Debezium, Kafka, Connect, destination, API proxy,
and browser path. Test artifacts are ignored and must be sanitized before
sharing.

Documentation is validated with `mkdocs build --strict`.
