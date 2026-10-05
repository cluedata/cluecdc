# Metadata schema baseline

This refactor deliberately resets Alembic history to revision `0001`. It is a
breaking metadata-schema change, not an in-place upgrade for old databases.

The audit checked Git tags, release documentation, migration references,
Compose startup, Kubernetes initialization, CI and tests. There were no release
tags or documented stable migration compatibility promise. The old chain
introduced and removed abandoned models and persisted source/destination
mirrors. The new baseline contains only the current control-plane schema,
including renewable worker and notification leases.

## Fresh installations

Run `alembic upgrade head` against an empty metadata database. Compose runs this
before starting the API; Kubernetes uses a migration init container. Verify
with `alembic check`. Coordinate migrations once per release before scaling
API replicas; a distributed migration lock is not supplied.

## Existing installations

Back up metadata and its encryption key first. Do not stamp an old database as
`0001`: its tables and constraints differ. No automatic conversion is supplied.

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
