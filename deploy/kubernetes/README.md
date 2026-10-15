# Kubernetes production base

This Kustomize base deploys only the ClueCDC web, API, and worker tiers. It
requires independently operated PostgreSQL, Kafka, and Kafka Connect services;
`metadata.yaml` and `connect.yaml` are retained as development references but
are deliberately absent from `kustomization.yaml`.

Required Secret name: `cluecdc-secrets`. Required keys:

- `database-url`: authenticated external PostgreSQL SQLAlchemy URL
- `secret-encryption-key`: Fernet key preserved with metadata backups
- `connect-secret-token`: shared only with approved Kafka Connect workers
- `session-secret`: unique random value of at least 32 characters

Provision the Secret outside this repository with the cluster's approved
external-secret workflow. The rendered manifests contain neither a Secret nor
plaintext credentials, and service-account token automounting is disabled.

The base is production-only: session authentication, an HTTPS public origin,
two API replicas, two workers, two web replicas, restricted pod security,
read-only root filesystems, bounded scratch volumes, resource bounds, startup /
readiness / liveness probes, and a 30-second termination grace period. Replace
the example hostname and pin images to an immutable registry tag or digest in
an environment overlay.

Only the web Service is reachable through the TLS Ingress. API and web Services
remain ClusterIP. NetworkPolicy denies ingress by default, admits ingress-nginx
to web, and admits web, explicitly labelled Connect clients, and monitoring to
API. Adapt selectors to the installed ingress controller. Label an in-cluster
Connect pod `cluecdc.io/connect-client=true`; for a separate namespace, label
both its namespace and pod. An out-of-cluster Connect service needs an
equivalent private route to `/internal/secrets` and a narrowly scoped IP policy.

Run `python scripts/deployment_validation.py` after every overlay change. See
`docs/deployment/kubernetes.md` for dependency and rollout procedures.
