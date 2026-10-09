# Kubernetes

The `deploy/kubernetes` Kustomize base deploys the ClueCDC web/API, a separate
ClueCDC background worker, metadata PostgreSQL, and a two-worker Kafka Connect
distributed cluster. It expects an external Kafka cluster and prebuilt images.

## Prepare secrets

Create the namespace and secret without committing values:

```bash
kubectl create namespace cluecdc
kubectl -n cluecdc create secret generic cluecdc-secrets \
  --from-literal=metadata-password='REPLACE' \
  --from-literal=database-url='postgresql+asyncpg://cluecdc:REPLACE@cluecdc-metadata:5432/cluecdc' \
  --from-literal=secret-encryption-key='FERNET_KEY' \
  --from-literal=connect-secret-token='AT_LEAST_32_RANDOM_CHARACTERS' \
  --from-literal=session-secret='AT_LEAST_32_RANDOM_CHARACTERS'
```

The base uses `AUTH_MODE=session` and does not enable the public development
Admin. Review `configmap.yaml` for the HTTPS public URL, CORS origins, Kafka
bootstrap servers, image registry, storage class, and network-specific URLs.
Build and publish the web, API, and Connect images from this repository before
applying.

```bash
kubectl apply -k deploy/kubernetes
kubectl -n cluecdc rollout status statefulset/cluecdc-metadata
kubectl -n cluecdc rollout status deployment/cluecdc-api
kubectl -n cluecdc rollout status deployment/cluecdc-worker
kubectl -n cluecdc rollout status deployment/cluecdc-web
kubectl -n cluecdc rollout status deployment/kafka-connect
```

Create the first production Admin interactively after the API is ready:

```bash
kubectl -n cluecdc exec -it deployment/cluecdc-api -- \
  python -m app.cli create-admin --email admin@example.com
```

The command prompts for a password of at least 8 characters. Replace the email
with an operator-controlled address; this account is not a recovery backdoor.

## Operational properties

- Metadata uses a persistent volume. Back it up with the encryption key.
- An API init container applies Alembic migrations before API startup.
- Liveness/readiness probes and conservative resources are set for each workload.
- Connect uses distributed mode with three replicated internal topics; Kafka must have at least three brokers or the replication factors must be reduced deliberately.
- Web, API, ClueCDC workers and Connect can scale independently. Run migrations as a coordinated release step before increasing replicas; see [Scaling](scaling.md).
- The base exposes ClusterIP services only. Add an authenticated TLS ingress or gateway in an environment overlay.

The manifests are a maintainable baseline, not a full production platform. Kafka, backups, certificates, ingress, network policy, monitoring, and external secret synchronization remain operator responsibilities.
