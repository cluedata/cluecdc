# Production considerations

The Kubernetes Kustomize base contains only the ClueCDC application tiers and
requires external PostgreSQL, Kafka, and Kafka Connect. Docker Compose remains a
local/integration topology and is not the production baseline. A production
review must cover:

- highly available external Kafka and metadata PostgreSQL;
- source WAL/binlog and Kafka retention sized for maximum outage;
- image registry, digest pinning, SBOMs, vulnerability policy, and upgrade tests;
- TLS, Kafka authentication/ACLs, private Connect/API networks, and authenticated ingress;
- metadata backups together with the secret-encryption key;
- database least-privilege roles and reviewed DDL/evolution permissions;
- Connect internal-topic replication and consistent plugins/configuration across workers;
- logs, metrics, paging, capacity, disaster recovery, and connector replay exercises.

Apply Alembic migrations before rolling out a new API. Review `CHANGELOG.md` for breaking behavior. Test connector upgrades against representative schemas and retained data before production rollout.

Validate every Kustomize overlay with `python scripts/deployment_validation.py`.
The base enforces restricted pod/container contexts, private ClusterIP services,
TLS ingress, ingress NetworkPolicy, health probes, graceful shutdown, and exact
version tags. Prefer image digests or registry-enforced immutable tags in the
environment overlay.
