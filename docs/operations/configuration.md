# Configuration reference

Run `python scripts/bootstrap.py` before starting Compose. The root `.env.example`
is the only template. Keep the generated `.env` private. The API uses one Pydantic settings
schema and fails at startup when required or security-sensitive values are
invalid. The web server validates its private API origin at request time.

<a id="optional-unique-local-secrets"></a>

## Local credentials

Before the first startup run:

```bash
python scripts/bootstrap.py
```

This requires host Python 3.12+ (`python3` or `py -3` on some hosts).
Bootstrap generates `CLUECDC_LOCAL_PASSWORD` and independent cryptographic
`SESSION_SECRET`, `SECRET_ENCRYPTION_KEY`, and `CONNECT_SECRET_TOKEN` values.
It fills missing settings without printing secrets or changing nonempty values.
The public development Admin remains `admin@cluecdc.local` / `cluecdc-admin`.

Fresh metadata, PostgreSQL/MySQL demo roles, MinIO, and ClickHouse use the shared
local password. Roles and permissions remain separate. Existing per-service
overrides take precedence: `METADATA_PASSWORD`, `POSTGRES_DEMO_ADMIN_PASSWORD`,
`POSTGRES_DEMO_SOURCE_PASSWORD`, `POSTGRES_DEMO_DESTINATION_PASSWORD`, the matching
`MYSQL_DEMO_*` variables, `MINIO_SECRET_KEY`, and `CLICKHOUSE_TEST_PASSWORD`.
Shell values take precedence over `.env`. Optional experimental sources retain
their established fixture credentials. Never reuse the database password for
encryption, sessions, or service tokens.

## Existing installations

1. Back up `.env`, metadata, and the encryption key using the
   [backup procedure](backup-restore.md). Record the current Compose project name.
2. Keep the existing `.env`. Transfer established credential overrides from
   any previous private test env file or shell configuration before bootstrap.
3. Run bootstrap. If no shared password exists, historical absent demo defaults
   are pinned as explicit overrides. Custom credentials and all established keys,
   including public example encryption keys, remain unchanged.
4. Start with the same project name (default `cluecdc`) and the new filenames.
   Metadata/Kafka/demo volume keys, service DNS, Connect group, and internal topic
   names are unchanged. No volume cleanup is needed.
5. Check readiness, login, saved connections, and connector state. Recover a
   missing encryption key from backup if stored encrypted credentials exist;
   a newly generated key cannot decrypt them.

Changing `.env` does **not** rotate passwords in initialized database volumes.
To adopt the shared password deliberately change each database role password
through a SQL client, update the environment override and saved ClueCDC Connection
credentials together, then verify. Keep the old values until migration completes.
Do not rotate keys or remove volumes as part of this upgrade.

For a fresh installation let bootstrap create `.env` directly. An existing file
without a shared password is conservatively treated as a legacy installation.

## Production

For production, provision independent secrets through your secret manager,
disable `BOOTSTRAP_DEFAULT_ADMIN`, configure HTTPS/session authentication, and
follow [production considerations](../deployment/production.md). Generating
local secrets alone does not turn the demo topology into a production deployment;
production validation continues to reject known example values.

## Environment variables

| Variable                      | Required       | Purpose                                                |
| ----------------------------- | -------------- | ------------------------------------------------------ |
| `ENVIRONMENT`                 | No             | `development`, `test`, or `production`                 |
| `DATABASE_URL`                | API            | Async SQLAlchemy metadata URL; production requires authenticated PostgreSQL |
| `SECRET_ENCRYPTION_KEY`       | Yes            | URL-safe 32-byte Fernet key                            |
| `CONNECT_SECRET_TOKEN`        | Yes            | 32+ character machine credential for secret resolution |
| `AUTH_MODE`                   | No             | `session`; `developer`/`token` remain for compatibility |
| `BOOTSTRAP_DEFAULT_ADMIN`     | No             | Create the public Admin in development only             |
| `DEFAULT_ADMIN_EMAIL`         | Development    | Initial public Admin email                               |
| `DEFAULT_ADMIN_PASSWORD`      | Development    | Initial public Admin password (minimum 8 characters)     |
| `SESSION_SECRET`              | Production     | Unique 32+ character key for session token digests     |
| `SESSION_TTL_SECONDS`         | No             | Browser session lifetime (default 28800)                |
| `INVITE_TTL_SECONDS`          | No             | Invite lifetime (default 86400)                         |
| `PUBLIC_URL`                  | Yes            | External web origin used in generated invite links     |
| `AUTH_TOKENS_JSON`            | Legacy token mode | SHA-256 token hash to actor/role JSON mapping        |
| `CORS_ORIGINS`                | Production     | JSON array of explicit HTTPS origins including `PUBLIC_URL` |
| `LOG_LEVEL`                   | No             | `debug`, `info`, `warn`/`warning`, or `error`          |
| `WORKER_CONCURRENCY`          | No             | Bounded worker concurrency, 1–32 (default 4)            |
| `JOB_LEASE_SECONDS`           | No             | Renewable job lease, 30–3600 seconds (default 300)      |
| `JOB_MAX_ATTEMPTS`            | No             | Maximum failed/expired job attempts (default 3)        |
| `JOB_RETRY_BASE_SECONDS`      | No             | Initial automatic retry delay (default 2 seconds)      |
| `JOB_RETRY_MAX_SECONDS`       | No             | Maximum exponential retry delay (default 60 seconds)   |
| `JOB_RETRY_JITTER_RATIO`      | No             | Positive retry jitter ratio, 0–1 (default 0.2)         |
| `CONNECTOR_OPERATION_LEASE_SECONDS` | No        | Expiry for a crashed lifecycle operation (default 300) |
| `AUTO_RECOVERY_ENABLED`       | No             | Restart transiently failed sink tasks automatically (default true) |
| `AUTO_RECOVERY_MAX_ATTEMPTS`  | No             | Consecutive task restart limit per outage (default 5)  |
| `AUTO_RECOVERY_BASE_DELAY_SECONDS` | No        | Initial sink-task recovery cooldown (default 10 seconds) |
| `AUTO_RECOVERY_MAX_DELAY_SECONDS` | No         | Maximum exponential recovery cooldown (default 300 seconds) |
| `AUTO_RECOVERY_JITTER_RATIO`  | No             | Positive recovery-delay jitter ratio, 0–1 (default 0.2) |
| `RECONCILE_INTERVAL_SECONDS`  | No             | Runtime observation interval                           |
| `INFRASTRUCTURE_HEALTH_INTERVAL_SECONDS` | No | Kafka and Kafka Connect health interval (default 30s) |
| `INTEGRATION_TIMEOUT_SECONDS` | No             | External request timeout                               |
| `ALLOW_INSECURE_KAFKA`        | No             | Permit non-TLS Kafka in production after explicit review |
| `OBSERVABILITY_ENABLED`      | No             | Enable cached read-only collection (default true) |
| `OBSERVABILITY_INTERVAL_SECONDS` | No          | Collection interval, 30–300 seconds (default 60) |
| `OBSERVABILITY_EXPORTERS`    | No             | Explicit trusted worker-to-exporter mappings; see monitoring |
| `OBSERVABILITY_EXPORTER_TOKEN` | No           | Optional independent exporter bearer credential |
| `AZURE_MANAGED_IDENTITY_ENABLED` | No          | Enable Azure identity capability only on configured Azure hosts |
| `API_INTERNAL_URL`            | Web production | Private origin of the ClueCDC API                      |

Core/demo ports use `CLUECDC_WEB_PORT`, `CLUECDC_API_PORT`, `KAFKA_PORT`,
`KAFKA_CONNECT_PORT`, `METADATA_PORT` (demo only), `POSTGRES_DEMO_PORT`,
`MYSQL_DEMO_PORT`, and `MINIO_PORT`. They configure development infrastructure, not the ClueCDC domain
model. Kafka and Kafka Connect endpoints are registered resources because one
installation can manage user-selected infrastructure.

For all advanced defaults and bounds consult `apps/api/app/core/config.py`.
The [monitoring guide](monitoring.md) describes exporter trust. Settings absent
from Compose's environment mapping can be supplied using an explicit deployment
override or Kubernetes environment configuration.

`CLICKHOUSE_TEST_PORT` and `CLICKHOUSE_TEST_PASSWORD` overrides in `.env`
belong only to the optional ClickHouse integration fixture. BigQuery service
account JSON, Redshift/AWS credentials, and Synapse SQL/storage credentials are
Connection secrets entered through ClueCDC and encrypted in metadata; they are
not application environment variables. ADC, AWS default credentials, and Azure
managed identity are available only when the deployment platform supplies them.

Never put raw bearer tokens in `AUTH_TOKENS_JSON`; store SHA-256 hashes. Never
commit `.env`. Back up `SESSION_SECRET` and `SECRET_ENCRYPTION_KEY` with
metadata: changing the session secret signs out every user, while losing the
encryption key makes stored connection secrets unrecoverable.

Production validation runs while the API, worker, CLI, or migration process
loads configuration. It rejects wildcard or non-HTTPS CORS entries, URLs with
credentials or paths, missing metadata credentials, known development values,
low-diversity secrets, and reuse of one value for multiple security secrets.
Validation messages identify the setting but never include its value.
