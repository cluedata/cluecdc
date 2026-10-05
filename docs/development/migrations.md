# Metadata schema baseline

The current schema is defined by baseline revision `0001`. An empty checkpoint
recognizes the last prototype revision `5e2d8a9f1c30`; upgrading from that specific
revision converts the existing schema and preserves resource/credential IDs.
Other historical revisions are not automatically supported.

The audit checked Git tags, release documentation, migration references,
Compose startup, Kubernetes initialization, CI and tests. There were no release
tags or documented stable migration compatibility promise. The old chain
introduced and removed abandoned models and persisted source/destination
mirrors. The new baseline contains only the current control-plane schema,
including renewable worker and notification leases.

## Fresh installations

Run `alembic upgrade head` against an empty metadata database. The empty legacy
checkpoint does not create obsolete tables. Compose runs this
before starting the API; Kubernetes uses a migration init container. Verify
with `alembic check`. Coordinate migrations once per release before scaling
API replicas. PostgreSQL migrations take a database-scoped transactional advisory
lock to serialize concurrent startup migration attempts.

## Existing installations

Back up metadata and its encryption key first. Do not stamp an old database as
`0001`: its tables and constraints differ.

For revision `5e2d8a9f1c30`, `alembic upgrade head` (also invoked by Compose
startup) performs a transactional conversion: promotes missing source/destination
rows to connections without changing IDs, merges capabilities for existing IDs,
retargets foreign keys, adds delivery type and worker/notification leases, and
removes the now-redundant mirrors. It preserves secrets, connector names/configs,
pipelines, deliveries, discovered metadata and audit records. Legacy abandoned
RUNNING jobs and sending notifications are requeued. Unsupported providers or
conflicting identities/credential references abort the transaction rather than
discarding records. Verify with `alembic check` after upgrading.

Keep the backup until the environment is verified. This bridge has no automatic
data-preserving downgrade; rollback requires restoring the pre-upgrade backup.

For other revisions, use the fresh-database procedure below; there is no complete
historical upgrade chain.

Use a new, empty metadata database and recreate connections and control-plane
resources from reviewed configuration. External Kafka topics, connector
offsets, replication slots, publications and storage objects are not deleted
by this migration. Reconcile existing connector ownership and names before
deploying replacements to avoid duplicate capture or delivery. Retain the old
database until the replacement environment is verified.

For disposable local installations only, replacing the specifically identified
metadata volume is an option. Never use broad `down -v` commands where Kafka,
database or object-storage data must be retained.

## Model boundary

`connections` is the sole persisted reusable endpoint model. Source/destination
API surfaces are capability-filtered views. Legacy Python `Source` and
`Destination` names are aliases, not ORM tables. Legacy v1 response field names
remain for client compatibility. Captures, discovered tables, schema versions
and deliveries reference canonical connection foreign keys.
