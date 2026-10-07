# Control-plane refactor audit

## Scope and deletion decisions

The pre-change audit covered API models, repositories, schemas, routes,
providers, services, migrations and tests; Next.js routes, components,
contracts and tests; Java Connect extensions and Docker build stages; Compose
defaults/overlays; Kubernetes; CI, demo/load scripts, secrets verification;
and docs, licenses, release policy and Git history. The starting tree was clean.
Baseline API and web tests passed.

Reference checks covered imports, routes, tests, builds, Compose, CI and docs
before removal. Abandoned migration models had no live product paths. See
[Metadata schema baseline](migrations.md) for the compatibility decision.
Real filesystem routes replaced the catch-all/manual dispatcher. Obsolete
source/destination list components were removed after their entry points
switched to filtered canonical views. Useful v1 API and route aliases remain.
CloudBeaver remains a documented optional developer tool for fixture database
inspection; it is absent from default runtime.

## Findings and implemented changes

| Finding | Refactor |
| --- | --- |
| Duplicate connection/source/destination tables | One connection table, typed config, capabilities and direct foreign keys |
| No executable storage delivery path | Separate Aiven strategy, credential references, safe SDK probes and content-verifying MinIO acceptance |
| JDBC assumptions shared by every delivery | Strategy-specific config and topic subscriptions without storage SQL mappings |
| Background work inside API lifecycle | Independent worker, health heartbeat, bounded concurrency and graceful cancellation |
| Locks held during network calls | Short claims, renewable ownership leases, bounded retry/backoff; short notification claims |
| Queries per collection item | Batched dependency/relationship reads and constant-query-count regression tests |
| Catch-all routing | Filesystem pages, awaited params, loading/error/not-found boundaries and shared shell |
| Large mixed feature files | Feature-scoped components and thin compatibility exports |
| Fixture-heavy runtime | Six default services; fixture databases, workload and MinIO only in test overlay |

## Verification and operational boundaries

Tests cover secret/config validation, unique probe cleanup, strategy separation,
dependency guards, lease recovery, retry, cancellation and committed results.
A real PostgreSQL test races two claimants for one job and verifies fencing.
Integration acceptance inspects stored CDC object content, not just creation.

No distributed exactly-once external-effect guarantee is claimed. A crash
between remote action and persistence can trigger retry; retain idempotent
resource names and reconcile ownership. Coordinate API migrations. Kafka/Connect
HA, production identity, ingress TLS, bucket retention and disaster recovery
remain deployment concerns. The former payload-sampling endpoint now returns
HTTP 410; the legacy UI route explains direct Kafka/storage inspection instead.
Smoke tests read Kafka directly. Snapshot notification consumers read only
Debezium control notifications, not table CDC records.

## Dependency audit limitation

On 2026-10-07, the full `npm audit --audit-level=high` reports the unpatched
`braces` stack-exhaustion issue through Next.js's development-only ESLint /
fast-glob dependency chain. The [upstream advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
lists no patched version; npm's forced fix downgrades the Next ESLint integration
to an incompatible older major. No forced downgrade or hidden allowlist is
applied. CI blocks high findings in production dependencies and critical
findings in the complete dependency tree. The full high-threshold scan remains
documented evidence and will fail until upstream publishes a compatible fix.
Runtime and development exposure are assessed separately; functional
verification is not treated as a clean vulnerability audit.
