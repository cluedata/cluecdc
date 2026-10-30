# Configuration reference

Copy `.env.example` to `.env` for Compose. The API uses one Pydantic settings
schema and fails at startup when required or security-sensitive values are
invalid. The web server validates its private API origin at request time.

## Local demo defaults

The Compose quick start needs only Git and Docker with Compose: copy
`.env.example` to `.env` and run `docker compose up -d --build --wait`.
Python and Node.js run inside the images and need not be installed on the host.
The example contains public demo passwords, a session secret, an encryption key,
and a Connect token. Copying it does not generate unique values. Use this
configuration only on the default loopback bindings in `ENVIRONMENT=development`.
The API entrypoint applies migrations and creates the demo Admin automatically.

## Optional unique local secrets

Before the first startup of a new local installation, you can optionally run:

```bash
python scripts/bootstrap.py
```

This helper requires host Python 3.12+ (`python3` on some macOS/Linux systems,
or `py -3` with the Windows launcher). It replaces missing or recognized example
values for `METADATA_PASSWORD`, `SESSION_SECRET`, `SECRET_ENCRYPTION_KEY`, and
`CONNECT_SECRET_TOKEN`, preserving custom values and the public demo Admin.
CI uses it to give disposable test environments unique secrets; it is not a
required installation step.

Do not run the helper as a rotation procedure for an initialized stack. Database
volumes retain their original passwords, and stored connection secrets depend
on the original encryption key. Existing installations must preserve those
values or follow a coordinated rotation and data-migration plan.

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
| `API_INTERNAL_URL`            | Web production | Private origin of the ClueCDC API                      |

Compose port variables and demo database credentials are documented inline in
`.env.example`. They configure development infrastructure, not the ClueCDC domain
model. Kafka and Kafka Connect endpoints are registered resources because one
installation can manage user-selected infrastructure.

`CLICKHOUSE_TEST_PORT` and `CLICKHOUSE_TEST_PASSWORD` in `.env.test.example`
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
