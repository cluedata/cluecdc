import importlib.util
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select, text

from app.models.entities import (
    Base,
    ConnectCluster,
    Connection,
    Connector,
    Job,
    KafkaCluster,
    Pipeline,
    PipelineDestination,
    SecretReference,
)
from tests.test_worker_leases import postgres_factory  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "legacy_migration", Path(__file__).parents[1] / "migrations/legacy_5e2d8a9f1c30.py"
)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


async def legacy_schema(factory):
    """Construct only the supported prototype differences in an isolated schema."""
    async with factory() as db:
        secret = SecretReference(ciphertext="encrypted-fixture-never-decrypted")
        db.add(secret)
        await db.flush()
        config = {
            "host": "database",
            "port": 5432,
            "database_name": "commerce",
            "username": "cdc",
            "environment": "DEV",
            "ssl_enabled": False,
            "provider_options": {},
        }
        source = Connection(
            name="Source",
            category="DATABASE",
            provider="POSTGRESQL",
            capabilities_json=["SOURCE"],
            config_json=config,
            secret_ref=secret.id,
        )
        target = Connection(
            name="Target",
            category="DATABASE",
            provider="POSTGRESQL",
            capabilities_json=["DESTINATION"],
            config_json=config,
            secret_ref=secret.id,
        )
        kafka = KafkaCluster(name="Kafka", bootstrap_servers="kafka:9092")
        db.add_all([source, target, kafka])
        await db.flush()
        cluster = ConnectCluster(
            name="Connect", base_url="http://connect:8083", kafka_cluster_id=kafka.id
        )
        db.add(cluster)
        await db.flush()
        connector = Connector(
            name="existing-source-name",
            connect_cluster_id=cluster.id,
            connector_class="source",
            config_json={"database.password": "${cluecdc:" + str(secret.id) + ":password}"},
        )
        db.add(connector)
        await db.flush()
        pipeline = Pipeline(
            name="Capture",
            source_id=source.id,
            kafka_cluster_id=kafka.id,
            connect_cluster_id=cluster.id,
            connector_id=connector.id,
            topic_prefix="unchanged-topic",
            snapshot_mode="initial",
        )
        db.add(pipeline)
        await db.flush()
        delivery = PipelineDestination(
            name="Delivery", pipeline_id=pipeline.id, destination_id=target.id
        )
        job = Job(kind="health", resource_id=source.id, actor="test", status="RUNNING")
        db.add_all([delivery, job])
        await db.commit()
        identifiers = (source.id, target.id, pipeline.id, delivery.id, secret.id, connector.id)
    async with factory.kw["bind"].begin() as db:
        for mirror in ("sources", "destinations"):
            await db.execute(
                text(f"""CREATE TABLE {mirror} (
                id uuid PRIMARY KEY, name varchar(120) NOT NULL UNIQUE,
                type varchar(30) NOT NULL, description varchar(1000) NOT NULL,
                environment varchar(30) NOT NULL, host varchar(255) NOT NULL,
                port integer NOT NULL, database_name varchar(128) NOT NULL,
                username varchar(128) NOT NULL,
                secret_ref uuid NOT NULL REFERENCES secret_references(id),
                ssl_enabled boolean NOT NULL, status varchar NOT NULL,
                last_health_check_at timestamptz, provider_options json NOT NULL,
                created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
            )""")
            )
            capability = "SOURCE" if mirror == "sources" else "DESTINATION"
            await db.execute(
                text(f"""INSERT INTO {mirror}
                SELECT id,name,lower(provider),description,'DEV','database',5432,'commerce','cdc',
                secret_ref,false,status,NULL,'{{}}'::json,created_at,updated_at
                FROM connections WHERE capabilities_json::jsonb ? :capability"""),
                {"capability": capability},
            )
        for table, old_column, new_column, mirror, ondelete in legacy.REFERENCES:

            def retarget(connection):
                operation = Operations(MigrationContext.configure(connection))
                fk = next(
                    item
                    for item in inspect(connection).get_foreign_keys(table)
                    if item["constrained_columns"] == [new_column]
                )
                operation.drop_constraint(fk["name"], table, type_="foreignkey")
                operation.alter_column(table, new_column, new_column_name=old_column)
                operation.create_foreign_key(
                    table + "_old_fk", table, mirror, [old_column], ["id"], ondelete=ondelete
                )
                for index in inspect(connection).get_indexes(table):
                    if index["name"] == f"ix_{table}_{new_column}":
                        operation.drop_index(index["name"], table_name=table)
                        operation.create_index(f"ix_{table}_{old_column}", table, [old_column])

            await db.run_sync(retarget)
        # This SOURCE existed only as a legacy row; its canonical resource must
        # be promoted while the existing destination identity must be reused.
        await db.execute(text("DELETE FROM connections WHERE id=:id"), {"id": source.id})
        for name in ("ix_jobs_claim", "ix_jobs_claimed_by", "ix_jobs_lease_expires_at"):
            await db.execute(text(f"DROP INDEX {name}"))
        for column in ("claimed_by", "lease_expires_at", "attempt_count", "next_attempt_at"):
            await db.execute(text(f"ALTER TABLE jobs DROP COLUMN {column}"))
        for column in ("claimed_by", "lease_expires_at"):
            await db.execute(text(f"ALTER TABLE notification_deliveries DROP COLUMN {column}"))
        await db.execute(text("ALTER TABLE pipeline_destinations DROP COLUMN delivery_type"))
        await db.execute(text("ALTER TABLE connections DROP CONSTRAINT ck_connections_category"))
        await db.execute(text("ALTER TABLE connections DROP CONSTRAINT ck_connections_provider"))
        await db.execute(
            text(
                "ALTER TABLE connections ADD CONSTRAINT ck_connections_category "
                "CHECK (category='DATABASE')"
            )
        )
    return identifiers


async def test_legacy_upgrade_preserves_resources_secrets_and_matches_baseline(postgres_factory):  # noqa: F811
    identifiers = await legacy_schema(postgres_factory)
    async with postgres_factory.kw["bind"].begin() as db:

        def convert(connection):
            with Operations.context(MigrationContext.configure(connection)):
                legacy.upgrade_legacy(connection)
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []

        await db.run_sync(convert)
    async with postgres_factory() as db:
        source, target, pipeline, delivery, secret, connector = identifiers
        assert (await db.get(Connection, source)).secret_ref == secret
        assert (await db.get(Connection, target)).secret_ref == secret
        assert (await db.get(Pipeline, pipeline)).source_id == source
        assert (await db.get(PipelineDestination, delivery)).destination_id == target
        assert (await db.get(PipelineDestination, delivery)).delivery_type == "DATABASE"
        assert (await db.get(Connector, connector)).name == "existing-source-name"
        assert (
            await db.get(SecretReference, secret)
        ).ciphertext == "encrypted-fixture-never-decrypted"
        assert (await db.scalar(select(Job))).status == "PENDING"


async def test_legacy_upgrade_refuses_conflict_and_rolls_back(postgres_factory):  # noqa: F811
    await legacy_schema(postgres_factory)
    async with postgres_factory.kw["bind"].begin() as db:
        await db.execute(text("UPDATE destinations SET type='unsupported'"))
    with pytest.raises(RuntimeError, match="Conflicting legacy endpoint"):
        async with postgres_factory.kw["bind"].begin() as db:

            def convert(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    legacy.upgrade_legacy(connection)

            await db.run_sync(convert)
    async with postgres_factory.kw["bind"].connect() as db:

        def verify(connection):
            assert inspect(connection).has_table("sources")
            assert "claimed_by" not in {c["name"] for c in inspect(connection).get_columns("jobs")}

        await db.run_sync(verify)


async def test_legacy_upgrade_disambiguates_long_connection_names(postgres_factory):  # noqa: F811
    source, target, *_ = await legacy_schema(postgres_factory)
    async with postgres_factory.kw["bind"].begin() as db:
        name = "x" * 120
        await db.execute(text("UPDATE sources SET name=:name"), {"name": name})
        await db.execute(text("UPDATE connections SET name=:name"), {"name": name})

        def convert(connection):
            with Operations.context(MigrationContext.configure(connection)):
                legacy.upgrade_legacy(connection)

        await db.run_sync(convert)
    async with postgres_factory() as db:
        promoted = await db.get(Connection, source)
        existing = await db.get(Connection, target)
        assert len(promoted.name) == 120
        assert promoted.name.endswith(f"(source {source})")
        assert promoted.name != existing.name
