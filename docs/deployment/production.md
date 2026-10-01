# Production considerations

The supplied deployments are starting points. A production review must cover:

- highly available external Kafka and metadata PostgreSQL;
- source WAL/binlog and Kafka retention sized for maximum outage;
- image registry, digest pinning, SBOMs, vulnerability policy, and upgrade tests;
- TLS, Kafka authentication/ACLs, private Connect/API networks, and authenticated ingress;
- metadata backups together with the secret-encryption key;
- database least-privilege roles and reviewed DDL/evolution permissions;
- Connect internal-topic replication and consistent plugins/configuration across workers;
- logs, metrics, paging, capacity, disaster recovery, and connector replay exercises.

Apply Alembic migrations before rolling out a new API. Review `CHANGELOG.md` for breaking behavior. Test connector upgrades against representative schemas and retained data before production rollout.
