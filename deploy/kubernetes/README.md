# Kubernetes baseline

This Kustomize base deploys ClueCDC, PostgreSQL metadata, and distributed Kafka
Connect. It deliberately does not install Kafka. Read `docs/deployment/kubernetes.md`
before applying.

Required Secret name: `cluecdc-secrets`. Required keys:

- `metadata-password`
- `database-url`
- `secret-encryption-key`
- `connect-secret-token`
- `session-secret` (at least 32 random characters)

Create environment overlays for images, ingress, storage class, Kafka addresses,
TLS, network policies, external secrets, and resource sizing.
