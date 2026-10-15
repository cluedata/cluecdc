# Metadata backup and restore

ClueCDC metadata PostgreSQL contains control-plane configuration, users,
sessions, audit history, connector associations, and encrypted connection
secrets. CDC records, Kafka offsets, Kafka topics, source WAL/binlogs,
destination data, and Kafka Connect internal state are not part of this backup.
A metadata restore therefore does not rewind or recreate the data plane.

## Backup

Install PostgreSQL client tools compatible with the metadata server. Supply the
database URL and Fernet key only through environment variables; do not put a URL
containing a password on the command line.

```bash
export DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST:5432/cluecdc'
export SECRET_ENCRYPTION_KEY='VALUE_FROM_THE_DEPLOYMENT_SECRET'
python scripts/metadata_backup.py backup \
  --output /secure-backups/cluecdc-$(date +%Y%m%dT%H%M%SZ).dump
```

The command uses `pg_dump --format=custom --no-owner --no-acl`, parses the
archive with `pg_restore --list`, and writes a sidecar manifest containing its
SHA-256, size, Alembic revision, and a one-way encryption-key fingerprint. It
never stores the database URL, database password, or encryption key. Existing
dump/manifest paths are never overwritten; an incomplete `.partial` file is
removed after a failed attempt.

Verify a stored copy periodically and after transfer:

```bash
python scripts/metadata_backup.py verify \
  --dump /secure-backups/cluecdc-20261014T020000Z.dump
```

Keep the dump and `SECRET_ENCRYPTION_KEY` in separate encrypted systems with
independent access control. Losing the key makes stored connection credentials
unrecoverable. Rotating the key requires the documented in-place secret
rotation before retiring the old key.

## Restore rehearsal and recovery

Create a new empty PostgreSQL database. Never point the restore command at the
active metadata database. The exact confirmation value is mandatory and the
tool independently rejects any target containing tables.

```bash
createdb cluecdc_restore_20261014
export RESTORE_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST:5432/cluecdc_restore_20261014'
export SECRET_ENCRYPTION_KEY='THE_KEY_USED_BY_THE_BACKUP'
python scripts/metadata_backup.py restore \
  --dump /secure-backups/cluecdc-20261014T020000Z.dump \
  --target-database-url-env RESTORE_DATABASE_URL \
  --confirm-target-database cluecdc_restore_20261014
```

Restore runs in one `pg_restore` transaction, upgrades to the current Alembic
head, runs `alembic check`, counts critical metadata tables, and decrypts every
secret reference without printing its value. Any failure leaves the isolated
database in place for diagnosis and never promotes, drops, or retries it
automatically. After verification, configure a non-production API instance to
use the isolated database and inspect users, pipelines, deliveries, alerts, and
audit history before planning a controlled cutover.

## Schedule, retention, RPO, and RTO

- Start with daily backups plus a restore rehearsal at least monthly. Increase
  frequency to match the acceptable metadata RPO; high-change installations
  commonly combine daily custom dumps with PostgreSQL WAL/PITR managed by the
  database platform.
- Retain multiple generations across the application-upgrade window and keep at
  least one independently stored copy. Apply organizational privacy retention
  to users, audit history, and encrypted credentials in the dump.
- Measure RTO using a representative database, including archive retrieval,
  database creation, restore, migrations, secret verification, application
  validation, and controlled endpoint cutover.
- A successful metadata restore does not prove Kafka offset or source-log
  availability. Separately validate Kafka/Connect internal topics, connector
  configs, PostgreSQL slots/WAL, MySQL binlogs, destinations, and outstanding
  incidents before resuming operations. Never reset offsets implicitly.
