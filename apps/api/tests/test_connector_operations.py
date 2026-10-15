from datetime import timedelta

import pytest

from app.core.errors import DomainError
from app.models.entities import ConnectCluster, Connector, KafkaCluster, now
from app.services.connector_operations import claim, release


async def connector_id(factory):
    async with factory() as db:
        kafka = KafkaCluster(name="operation-kafka", bootstrap_servers="kafka:9092")
        db.add(kafka)
        await db.flush()
        cluster = ConnectCluster(
            name="operation-connect",
            base_url="http://connect:8083",
            kafka_cluster_id=kafka.id,
        )
        db.add(cluster)
        await db.flush()
        connector = Connector(
            name="operation-connector",
            connect_cluster_id=cluster.id,
            connector_class="example.Connector",
            config_json={},
        )
        db.add(connector)
        await db.commit()
        return connector.id


async def test_connector_operation_lease_rejects_overlap_and_recovers_stale_owner(db_factory):
    identifier = await connector_id(db_factory)
    async with db_factory() as first:
        token, _ = await claim(first, identifier, "pause")

    async with db_factory() as second:
        with pytest.raises(DomainError) as exc:
            await claim(second, identifier, "resume")
        assert exc.value.code == "CONNECTOR_OPERATION_ACTIVE"

    async with db_factory() as db:
        connector = await db.get(Connector, identifier)
        connector.operation_started_at = now() - timedelta(hours=1)
        await db.commit()
    async with db_factory() as replacement:
        replacement_token, connector = await claim(replacement, identifier, "resume")
        assert replacement_token != token
        assert connector.operation_name == "resume"
        await release(replacement, identifier, replacement_token)
    async with db_factory() as db:
        connector = await db.get(Connector, identifier)
        assert connector.operation_token is None
