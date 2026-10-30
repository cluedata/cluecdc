# Kubernetes

The `deploy/kubernetes` Kustomize base is a production application baseline. It
deploys the ClueCDC web/API/worker tiers and intentionally does **not** deploy a
database, Kafka broker, or Kafka Connect. PostgreSQL, Kafka, and Connect must be
managed or operated independently. The unreferenced `metadata.yaml` and
`connect.yaml` files are local-development references, not production resources.

## External dependencies

Before rollout, provide:

- a backed-up, highly available PostgreSQL database reachable by API and workers;
- a TLS-authenticated Kafka cluster reachable by ClueCDC and Connect;
- a distributed Kafka Connect cluster with identical ClueCDC/Debezium plugins on
  every worker, replicated internal topics, and private REST connectivity;
- private Connect-to-API connectivity for secret resolution.

Register the external Kafka and Kafka Connect REST endpoints through ClueCDC's
connection API/UI. Kafka client credentials are encrypted metadata. Kafka
Connect worker credentials remain independent deployment configuration.

External Connect workers using `${cluecdc:...}` references must include the
extension produced by `infrastructure/docker/connect.Dockerfile` and configure:

```text
CONNECT_CONFIG_PROVIDERS=cluecdc
CONNECT_CONFIG_PROVIDERS_CLUECDC_CLASS=io.cluecdc.connect.ClueSecretConfigProvider
CONNECT_CONFIG_PROVIDERS_CLUECDC_PARAM_URL=http://cluecdc-api.cluecdc.svc.cluster.local:8000
CLUECDC_SECRETS_TOKEN=<same externally managed connect-secret-token>
```

The same image also contains the v0.3.2 ClickHouse and BigQuery sink plugins and
the warehouse history SMT. Use the identical immutable image digest on every
Connect worker. Redshift and Synapse staged deliveries run in the ClueCDC worker,
so worker egress must additionally allow Kafka, the warehouse SQL endpoint, and
the configured S3/Azure Storage endpoints. Grant only the configured staging
prefix; do not expose cloud credentials through ConfigMaps.

Use an equivalent private HTTPS URL when Connect runs outside the cluster. Never
publish `/internal/secrets` to the public Ingress. Label an in-cluster Connect
pod `cluecdc.io/connect-client=true`; when it is in another namespace, label
that namespace with the same key so the base NetworkPolicy admits it. Replace
the policy with reviewed private CIDRs when the caller is outside Kubernetes.

## Prepare configuration and secrets

Create an environment overlay that replaces the example public hostname,
Ingress class/TLS secret, resource sizing, and the exact `0.2.1` image tags with
registry-enforced immutable tags or digests. The base never contains Secret
values.

Create the namespace, then provision `cluecdc-secrets` through the approved
external-secret controller or, for a one-time operator workflow, stdin:

```bash
kubectl apply -f deploy/kubernetes/namespace.yaml
kubectl -n cluecdc create secret generic cluecdc-secrets \
  --from-literal=database-url='postgresql+asyncpg://USER:PASSWORD@DB-HOST:5432/cluecdc' \
  --from-literal=secret-encryption-key='FERNET_KEY' \
  --from-literal=connect-secret-token='AT_LEAST_32_RANDOM_CHARACTERS' \
  --from-literal=session-secret='ANOTHER_32_PLUS_RANDOM_CHARACTERS' \
  --dry-run=client -o yaml | kubectl apply -f -
```

Do not put those values in an overlay, command history, or Git. Require database
TLS according to the PostgreSQL provider's connection policy. Preserve the
encryption key separately from database backups.

Production configuration fixes `ENVIRONMENT=production`, `AUTH_MODE=session`,
and never enables the development Admin bootstrap. Replace `PUBLIC_URL` and
`CORS_ORIGINS` together with the same HTTPS origin.

## Validate and roll out

Render and enforce the repository's production policy before contacting a
cluster:

```bash
kubectl kustomize deploy/kubernetes > /secure/review/cluecdc-rendered.yaml
python scripts/deployment_validation.py --kustomize-dir deploy/kubernetes
```

For an upgrade, first run the documented metadata backup and guarded upgrade.
The API init container runs `alembic upgrade head` under the migration advisory
lock, so concurrent API starts serialize and a fresh install is initialized.
It does not replace the pre-upgrade backup procedure.

Pass the environment overlay directory to `--kustomize-dir` when validating an
overlay. Run `scripts/deployment_runtime.py --api-image IMAGE --web-image IMAGE`
to verify the built containers against a disposable PostgreSQL instance with
random production secrets, read-only filesystems, and SIGTERM shutdown. The
harness cleans up only its own UUID-scoped containers, network and test volumes.

```bash
kubectl apply -k deploy/kubernetes
kubectl -n cluecdc rollout status deployment/cluecdc-api --timeout=5m
kubectl -n cluecdc rollout status deployment/cluecdc-worker --timeout=5m
kubectl -n cluecdc rollout status deployment/cluecdc-web --timeout=5m
kubectl -n cluecdc get pods,deploy,networkpolicy,ingress
```

Create the first production Admin interactively after API readiness:

```bash
kubectl -n cluecdc exec -it deployment/cluecdc-api -- \
  python -m app.cli create-admin --email admin@example.com
```

## Runtime properties

- Startup probes protect slow initialization; readiness removes unhealthy pods
  from Services; liveness restarts stuck processes.
- Kubernetes sends SIGTERM and grants 30 seconds. Uvicorn drains for up to 25
  seconds; workers cancel in-flight jobs and release their leases for retry.
- Two worker replicas are supported by row/job leases and connector-operation
  leases. Keep `JOB_LEASE_SECONDS` longer than the maximum normal operation and
  size `WORKER_CONCURRENCY` against external dependency capacity.
- Root filesystems are read-only. Explicit bounded `emptyDir` volumes provide
  only temporary TLS files, worker heartbeat state, and the web cache.
- Only the web tier has public ingress. API metrics, secret resolution, Connect
  REST, PostgreSQL, and Kafka stay on private networks.
- The included policy controls ingress only because external service CIDRs and
  DNS vary by environment. Add reviewed egress policy in the overlay without
  blocking DNS, PostgreSQL, Kafka, Connect REST, or required HTTPS endpoints.

Back up metadata plus its encryption key, monitor each external dependency, and
test failure recovery before promotion. See [backup and restore](../operations/backup-restore.md),
[migrations](../development/migrations.md), and [scaling](scaling.md).

## Isolated cluster acceptance

The `kubernetes-hardening` job in `.github/workflows/hardening.yml` provisions a
dedicated kind cluster with pinned, checksum-verified Calico and ingress-nginx
fixtures. These are test dependencies; the production base does not install
them. After checksum verification, the fixture preparation removes immediate
TTL cleanup from the two ingress admission Jobs so sequential completion waits
cannot race Job deletion. Both Jobs must still complete; cluster teardown
removes them. The setup follows the upstream [kind](https://kind.sigs.k8s.io/docs/user/quick-start/)
and [Calico on kind](https://docs.tigera.io/calico/latest/getting-started/kubernetes/kind)
procedures, with the default CNI disabled so policy enforcement is tested.

After preparing a dedicated `cluecdc-hardening-*` kind cluster with those
controllers and a separate kubeconfig, run:

```bash
python scripts/kubernetes_acceptance.py \
  --kind /path/to/kind --cluster-name cluecdc-hardening-local \
  --kubeconfig /private/test-kubeconfig \
  --api-image cluecdc-api:k8s --web-image cluecdc-web:k8s
```

The harness checks kind ownership and refuses an existing `cluecdc` namespace.
It loads local application images, starts external PostgreSQL with random
credentials, supplies application/TLS Secrets through stdin, and applies the
rendered production base with unique test image tags. It verifies two replicas
per tier, concurrent migration startup and drift, real Calico allow/deny and
namespace-label revocation, verified HTTPS/HTTP redirect, production login,
Secure cookies, CSRF rejection, and worker rolling restart. Only its namespaces
and external database container are removed. The caller removes the dedicated
kind cluster afterward; the user's default kubeconfig is not used.

The release hardening workflow requires this job as well as the live CDC and
browser job. Keep target-cluster sizing, broker ACL/identity, availability and
environment-specific network review as operator promotion checks.
