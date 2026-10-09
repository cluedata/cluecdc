# Configuration reference

Copy `.env.example` to `.env` for Compose. The API uses one Pydantic settings
schema and fails at startup when required or security-sensitive values are
invalid. The web server validates its private API origin at request time.

| Variable                      | Required       | Purpose                                                |
| ----------------------------- | -------------- | ------------------------------------------------------ |
| `ENVIRONMENT`                 | No             | `development`, `test`, or `production`                 |
| `DATABASE_URL`                | API            | Async SQLAlchemy metadata URL; Compose constructs it   |
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
| `CORS_ORIGINS`                | No             | JSON array of explicitly allowed web origins           |
| `LOG_LEVEL`                   | No             | `debug`, `info`, `warn`/`warning`, or `error`          |
| `WORKER_CONCURRENCY`          | No             | Bounded worker concurrency, 1–32 (default 4)            |
| `JOB_LEASE_SECONDS`           | No             | Renewable job lease, 30–3600 seconds (default 300)      |
| `JOB_MAX_ATTEMPTS`            | No             | Maximum failed/expired job attempts (default 3)        |
| `RECONCILE_INTERVAL_SECONDS`  | No             | Runtime observation interval                           |
| `INTEGRATION_TIMEOUT_SECONDS` | No             | External request timeout                               |
| `API_INTERNAL_URL`            | Web production | Private origin of the ClueCDC API                      |

Compose port variables and demo database credentials are documented inline in
`.env.example`. They configure development infrastructure, not the ClueCDC domain
model. Kafka and Kafka Connect endpoints are registered resources because one
installation can manage user-selected infrastructure.

Never put raw bearer tokens in `AUTH_TOKENS_JSON`; store SHA-256 hashes. Never
commit `.env`. Back up `SESSION_SECRET` and `SECRET_ENCRYPTION_KEY` with
metadata: changing the session secret signs out every user, while losing the
encryption key makes stored connection secrets unrecoverable.
