# Security

Set `ENVIRONMENT=production`, `AUTH_MODE=session`, an HTTPS `PUBLIC_URL`, and
a unique 32+ character `SESSION_SECRET`. Production startup rejects developer
or legacy token authentication, weak/example session secrets, non-HTTPS public
URLs, and known example service/encryption secrets. Bootstrap the first Admin
with `python -m app.cli create-admin`. Set `BOOTSTRAP_DEFAULT_ADMIN=false`;
production rejects the public development-account bootstrap when enabled.
`DATABASE_URL` must identify an authenticated PostgreSQL database. Configure at
least one explicit HTTPS `CORS_ORIGINS` entry and include the normalized
`PUBLIC_URL`; wildcard origins and origins containing paths or credentials are
rejected before readiness.

Cookie-authenticated production writes require an `Origin` matching the
configured allowlist. The same-origin Next.js API proxy forwards only the
browser `Origin` needed for that check and does not forward client-supplied
`X-Forwarded-*` headers. Session cookies are `Secure`, `HttpOnly`,
`SameSite=Lax`, scoped to `/`, and carry matching maximum-age and expiry values.
Responses add clickjacking, MIME-sniffing, referrer, browser-feature, and web
Content Security Policy headers.

Terminate TLS and redirect HTTP at a reviewed reverse proxy or Ingress. The
Kustomize baseline includes an nginx Ingress example using an externally
provided `cluecdc-tls` Secret. Replace the hostname/class in an overlay and
configure HSTS only after verifying that every public route is HTTPS. Local
development intentionally remains HTTP and does not set Secure cookies.

Database and object-store credentials are encrypted with Fernet in metadata. The encryption key is external configuration and must be backed up separately. Kafka Connect receives secret references and calls a private internal endpoint using `CONNECT_SECRET_TOKEN`; isolate this path and add transport protection appropriate to the environment.

Provide `database-url`, `secret-encryption-key`, `connect-secret-token`, and
`session-secret` through the externally managed `cluecdc-secrets` Kubernetes
Secret. The manifests contain references only and disable service-account token
mounting; ClueCDC workloads do not need Kubernetes API permission to list or
read Secrets. Do not commit a rendered Secret or plaintext `stringData` file.

Credential edits replace the ciphertext in the existing secret reference, so
deployed connector references remain stable. Rotate the credential at the
external database/object store first, update ClueCDC immediately, validate the
connection and connector tasks, then revoke the old credential. This sequence
can cause a short reconnect window and should be scheduled accordingly.

Back up `SECRET_ENCRYPTION_KEY` separately from the database backup, under the
same access and recovery controls. A restored database is intentionally
unreadable without the exact key. This release does not support online
encryption-key rotation; do not replace the key in-place. Restore the original
key, or migrate credentials in a tested maintenance procedure before changing
it. Rotating `CONNECT_SECRET_TOKEN` requires coordinated API/worker and every
Kafka Connect worker restart.

Restrict `/metrics`, OpenAPI, Kafka Connect REST, brokers, and databases to private networks. ClueCDC does not yet provide SSO, OIDC, automated TLS, Kafka ACL provisioning, or a production external-secret integration.

Report vulnerabilities using the private process in the repository `SECURITY.md`; never include credentials or customer event payloads in a public issue.
