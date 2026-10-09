# Security

Set `ENVIRONMENT=production`, `AUTH_MODE=session`, an HTTPS `PUBLIC_URL`, and
a unique 32+ character `SESSION_SECRET`. Production startup rejects developer
or legacy token authentication, weak/example session secrets, non-HTTPS public
URLs, and known example service/encryption secrets. Bootstrap the first Admin
with `python -m app.cli create-admin`. Set `BOOTSTRAP_DEFAULT_ADMIN=false`;
production rejects the public development-account bootstrap when enabled.

Database and object-store credentials are encrypted with Fernet in metadata. The encryption key is external configuration and must be backed up separately. Kafka Connect receives secret references and calls a private internal endpoint using `CONNECT_SECRET_TOKEN`; isolate this path and add transport protection appropriate to the environment.

Restrict `/metrics`, OpenAPI, Kafka Connect REST, brokers, and databases to private networks. ClueCDC does not yet provide SSO, OIDC, automated TLS, Kafka ACL provisioning, or a production external-secret integration.

Report vulnerabilities using the private process in the repository `SECURITY.md`; never include credentials or customer event payloads in a public issue.
