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

Provision that Secret outside this repository with your cluster's approved
secret workflow. The checked-in manifests intentionally contain neither a
Secret object nor plaintext values. Pods consume named keys through kubelet and
have service-account token automounting disabled; no ClueCDC Role grants Secret
list/get access.

Create environment overlays for images, ingress, storage class, Kafka addresses,
TLS, network policies, external secrets, and resource sizing.

The baseline Ingress terminates TLS with externally supplied Secret
`cluecdc-tls` and routes only to the web service. Replace its example hostname
and ingress class in an overlay. The nginx annotation forces HTTP-to-HTTPS at
the infrastructure boundary; use the equivalent setting for another controller.
The API and Kafka Connect services remain cluster-internal. ClueCDC does not
derive its public origin from forwarding headers, so a client cannot change the
CSRF trust boundary with `X-Forwarded-Host` or `X-Forwarded-Proto`.
