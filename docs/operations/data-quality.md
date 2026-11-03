# Operating Data Quality

Apply additive Alembic revision `0015` with the existing backup/upgrade workflow
before updating API/worker processes. It adds four `data_quality_*` tables and does
not change CDC offsets, pipelines, deliveries, secrets or warehouse ledgers.
Downgrade intentionally drops those four quality tables; preserve needed history
before an operator-directed downgrade.

Use SELECT-only accounts. Existing encrypted Connection secrets/adapters are
reused. PostgreSQL uses read-only transactions/local statement timeouts; MySQL
uses read-only transactions/SELECT timeouts/UTC sessions. Identifiers are validated
against live metadata and quoted; predicate values are typed and parameterized.
No arbitrary SQL, row sampling, data repair or production writes are available.
Evidence contains aggregates/schema metadata, not raw rows.

Full COUNT can scan the entire table; query timeout is not a physical row-scan
limit. Start manually and measure load. One-minute full counts require explicit
cost acknowledgment. Queries time out after at most 30 seconds; total Check timeout
is 5–180 seconds (default 120). Existing worker concurrency bounds active Jobs.
Each worker process permits one measurement session per database endpoint, with a
single connection/sequential queries. This is not a distributed database-wide rate
limiter: size replicas and schedules accordingly. Discovery returns at most 1,000
base tables; tables with more than 500 columns are unsupported. Only a Connection's
registered database can be selected.

The existing Job lease renewer owns execution. Results, counters and notification
outbox entries commit only while the lease is owned. Worker interruptions reuse
the run and existing bounded retry policy. Database comparison errors/timeouts are
terminal run ERRORs, not automatic expensive scan retries. Exhausted Jobs are
reaped, recorded as ERROR and release the overlap guard. There is no automatic
resnapshot, slot/topic deletion, checkpoint manipulation or repair.

Polling is approximately one second while idle. Row locks, compare-and-set and a
unique Check/occurrence identity prevent duplicate work. Downtime does not create
an unlimited catch-up backlog. Pause does not cancel queued/running work. Edits
preserve history; old-version runs do not alter new-version alert state. Deleting
a Check cancels publication from unfinished work and retains safe history.

Retention defaults to 90 days (1–3,650 configurable). Hourly cleanup removes
finished runs in bounded batches, up to 1,000 expired runs per sweep, using each
Check's retention setting. Job/audit retention
remains the existing control-plane policy. Idempotency keys cover retained runs.

Investigate FAIL using stored values, observation times, thresholds, windows and
schema differences. Verify dataset meaning/timestamp semantics before changing
thresholds. INCONCLUSIVE asks operators to establish comparability. Check existing
incident and delivery status for actual notification evidence; incident persistence
does not mean a message was delivered.

## Disposable reproduction

Use existing development dependencies, pytest and Playwright. The fixed loopback
ports/project below are reserved for disposable tests; never substitute the shared
operator Compose project.

```sh
docker compose -p cluecdc-dq-v034 -f infrastructure/tests/data-quality.compose.yaml up -d --wait
DQ_LIVE_TESTS=1 DQ_EVIDENCE_DIR=artifacts/data-quality WORKER_TEST_DATABASE_URL=postgresql+asyncpg://dq:dq-test-only@127.0.0.1:55435/dq python -m pytest apps/api/tests -q --junitxml=artifacts/data-quality/api.xml
python scripts/data_quality_fixture.py
```

PowerShell: set `$env:DQ_LIVE_TESTS='1'` and other variables separately before the
pytest command. The script never loads operator credentials. It seeds only
`dq_browser` metadata on 55435 and named datasets on PostgreSQL 55436/55437; live
MySQL scenarios use independent 33308/33309. Browser measurements use a SELECT-only
role; channels are public test fixtures and never production transports.

Start isolated processes in separate terminals:

```sh
python -c "import sys; sys.path.insert(0,'scripts'); import data_quality_fixture; import uvicorn; uvicorn.run('app.main:app',host='127.0.0.1',port=8014)"
python -c "import sys; sys.path.insert(0,'scripts'); import data_quality_fixture; import asyncio; from app.workers.worker import worker_loop; asyncio.run(worker_loop())"
API_INTERNAL_URL=http://127.0.0.1:8014 npm --workspace apps/web run dev -- --port 3014
```

Then:

```sh
DQ_BROWSER_TESTS=1 PLAYWRIGHT_BASE_URL=http://127.0.0.1:3014 npm --workspace apps/web run test:e2e -- tests/e2e/data-quality.spec.ts
docker compose -p cluecdc-dq-v034 -f infrastructure/tests/data-quality.compose.yaml down
```

The dedicated CI workflow runs the same real-database and Admin/Ops/Viewer browser
flows with the production frontend build. Stop only fixture-owned processes and
containers. No production messages are needed for verification.
