# Control plane

Metadata uses UUIDs, UTC timestamps, foreign keys, operational indexes, unique source/pipeline names, and unique topic prefixes. Alembic creates all tables; application startup does not use `create_all`. Alembic revisions contain explicit frozen DDL.

Canonical connections reference encrypted SecretReference records. Fernet
ciphertext is stored in metadata; the encryption key comes from deployment
configuration. API/config/audit responses omit passwords, storage credentials
and encrypted blobs. Validation responses omit input values; operational logs
omit bodies, auth headers, exception text and raw Connect traces. Credential
rotation updates ciphertext at the same reference ID; restart/redeploy affected
tasks to resolve updated credentials.

Kafka Connect receives `${cluecdc:<secret-uuid>:password}` in connector configs. The included Java ConfigProvider authenticates to `/internal/secrets/{uuid}` with a dedicated service token and receives the plaintext only in memory. The public `/api/v1` web proxy cannot route to that internal endpoint. The token is read directly from the worker environment, not written into worker configuration or connector topics. Production must protect this route with network policy and HTTPS/mTLS; the development Docker network uses HTTP.

All metadata mutations, lifecycle requests, job transitions and changed runtime
observations create audit records. The HTTP API and worker are separate
processes. Jobs use renewable leases and short SKIP LOCKED claim transactions;
external work runs after commit. Discovery locks the connection only for its
metadata merge after querying the source. Runtime observations close database
transactions before network calls. Notification dispatch follows the same
claim/call/persist pattern.

Config validation precedes pipeline persistence. Deployment validates again, creates the external connector without overwriting existing names, and persists the connector association. Persistence failure triggers compensating connector deletion. **There is an unavoidable crash/ambiguous-timeout window between an external REST side effect and metadata commit.** This release reports conflicts/unavailable results and logs failed compensation; automated orphan adoption and full outbox reconciliation are future work. Operators should inspect Connect inventory before retrying ambiguous deployments.

Developer mode assigns an explicit local Admin principal. Production accepts bearer identities mapped by SHA256 token hashes to actor/role. Business services receive actors without implementing authentication themselves. Viewer has read access; DataEngineer can create/operate pipelines and sources; PlatformAdmin can manage infrastructure and audit; Admin has all permissions. OIDC and token lifecycle management are later phases.

Connection is the only endpoint table. Pipeline source and Delivery destination
foreign keys reference canonical connections; legacy `/sources` and
`/destinations` APIs adapt their shape without creating mirror rows. Database
and object-storage delivery providers build distinct connector settings. Active
associations block deletion and connection edits. Collection endpoints batch
related resources and dependency counts. DataEngineer and PlatformAdmin have
destination read/write/operate permissions; Viewer remains read-only. Errors
retain destination/pipeline/connector associations and audited resolution.

Schema versions hash canonical JSON of ordered columns and PKs. Diff rules: nullable column added = NON_BREAKING, required column added = POTENTIALLY_BREAKING, column removed = BREAKING, type changed = POTENTIALLY_BREAKING, nullable tightened = BREAKING, nullable relaxed = NON_BREAKING, PK changed = BREAKING. Discovery does not currently create a dropped-table schema tombstone.
