# Kubernetes

The `deploy/kubernetes` Kustomize base deploys the ClueCDC web/API, metadata PostgreSQL, and a two-worker Kafka Connect distributed cluster. It expects an external Kafka cluster and prebuilt ClueCDC images. This keeps broker lifecycle outside the application manifest.

## Prepare secrets

Create the namespace and secret without committing values:

```bash
kubectl create namespace cluecdc
kubectl -n cluecdc create secret generic cluecdc-secrets \
  --from-literal=metadata-password='REPLACE' \
  --from-literal=database-url='postgresql+asyncpg://cluecdc:REPLACE@cluecdc-metadata:5432/cluecdc' \
  --from-literal=secret-encryption-key='FERNET_KEY' \
  --from-literal=connect-secret-token='AT_LEAST_32_RANDOM_CHARACTERS' \
  --from-literal=auth-tokens-json='{"SHA256_OF_TOKEN":{"actor":"operator","role":"Admin"}}'
```

Review `configmap.yaml` for Kafka bootstrap servers, public origins, image registry, storage class, and network-specific URLs. Build and publish the web, API, and Connect images from this repository before applying.

```bash
kubectl apply -k deploy/kubernetes
kubectl -n cluecdc rollout status statefulset/cluecdc-metadata
kubectl -n cluecdc rollout status deployment/cluecdc-api
kubectl -n cluecdc rollout status deployment/cluecdc-web
kubectl -n cluecdc rollout status deployment/kafka-connect
```

## Operational properties

- Metadata uses a persistent volume. Back it up with the encryption key.
- An API init container applies Alembic migrations before API startup.
- Liveness/readiness probes and conservative resources are set for each workload.
- Connect uses distributed mode with three replicated internal topics; Kafka must have at least three brokers or the replication factors must be reduced deliberately.
- Web and Connect can scale horizontally. Keep the API at one replica while its in-process reconciliation/notification worker is enabled; see [Scaling](scaling.md).
- The base exposes ClusterIP services only. Add an authenticated TLS ingress or gateway in an environment overlay.

The manifests are a maintainable baseline, not a full production platform. Kafka, backups, certificates, ingress, network policy, monitoring, and external secret synchronization remain operator responsibilities.
