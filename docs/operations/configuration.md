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
| `AUTH_MODE`                   | No             | `developer` or `token`; production requires `token`    |
| `AUTH_TOKENS_JSON`            | Token mode     | SHA-256 token hash to actor/role JSON mapping          |
| `CORS_ORIGINS`                | No             | JSON array of explicitly allowed web origins           |
| `LOG_LEVEL`                   | No             | `debug`, `info`, `warn`/`warning`, or `error`          |
| `WORKER_ENABLED`              | No             | Enables durable jobs and reconciliation                |
| `RECONCILE_INTERVAL_SECONDS`  | No             | Runtime observation interval                           |
| `INTEGRATION_TIMEOUT_SECONDS` | No             | External request timeout                               |
| `API_INTERNAL_URL`            | Web production | Private origin of the ClueCDC API                      |

Compose port variables and demo database credentials are documented inline in
`.env.example`. They configure development infrastructure, not the ClueCDC domain
model. Kafka and Kafka Connect endpoints are registered resources because one
installation can manage user-selected infrastructure.

Never put raw bearer tokens in `AUTH_TOKENS_JSON`; store SHA-256 hashes. Never
commit `.env`. Back up `SECRET_ENCRYPTION_KEY` with metadata: losing it makes
stored connection secrets unrecoverable.
