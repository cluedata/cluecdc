# Security

Set `ENVIRONMENT=production` and `AUTH_MODE=token`. Production startup rejects developer authentication and known example secret values. Configure bearer tokens as SHA-256 hashes in `AUTH_TOKENS_JSON`; raw tokens are not stored there.

Database and object-store credentials are encrypted with Fernet in metadata. The encryption key is external configuration and must be backed up separately. Kafka Connect receives secret references and calls a private internal endpoint using `CONNECT_SECRET_TOKEN`; isolate this path and add transport protection appropriate to the environment.

Restrict `/metrics`, OpenAPI, Kafka Connect REST, brokers, and databases to private networks. ClueCDC does not yet provide SSO, OIDC, automated TLS, Kafka ACL provisioning, or a production external-secret integration.

Report vulnerabilities using the private process in the repository `SECURITY.md`; never include credentials or customer event payloads in a public issue.
