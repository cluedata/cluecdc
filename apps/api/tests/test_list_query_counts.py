from uuid import uuid4

from sqlalchemy import event

from app.models.entities import (
    ConnectCluster,
    Connection,
    Connector,
    KafkaCluster,
    Pipeline,
    PipelineDestination,
    PipelineTable,
)


async def add_resources(factory, count):
    async with factory() as db:
        source = Connection(
            name="shared source " + uuid4().hex,
            category="DATABASE",
            provider="POSTGRESQL",
            capabilities_json=["SOURCE"],
            config_json={
                "host": "source",
                "port": 5432,
                "database_name": "commerce",
                "username": "cdc",
            },
        )
        target = Connection(
            name="shared target " + uuid4().hex,
            category="OBJECT_STORAGE",
            provider="MINIO",
            capabilities_json=["DESTINATION"],
            config_json={"bucket": "cdc-archive", "endpoint": "http://minio:9000"},
        )
        kafka = KafkaCluster(name="kafka " + uuid4().hex, bootstrap_servers="kafka:29092")
        db.add_all([source, target, kafka])
        await db.flush()
        connect = ConnectCluster(
            name="connect " + uuid4().hex, base_url="http://connect:8083", kafka_cluster_id=kafka.id
        )
        db.add(connect)
        await db.flush()
        for _ in range(count):
            prefix = "capture_" + uuid4().hex
            pipeline = Pipeline(
                name=prefix,
                source_id=source.id,
                kafka_cluster_id=kafka.id,
                connect_cluster_id=connect.id,
                topic_prefix=prefix,
                snapshot_mode="no_data",
            )
            connector = Connector(
                name="sink-" + prefix,
                connector_type="sink",
                connect_cluster_id=connect.id,
                connector_class="io.aiven.kafka.connect.s3.AivenKafkaConnectS3SinkConnector",
                config_json={},
            )
            db.add_all([pipeline, connector])
            await db.flush()
            db.add(
                PipelineTable(
                    pipeline_id=pipeline.id,
                    schema_name="public",
                    table_name="customers",
                    topic_name=prefix + ".public.customers",
                )
            )
            db.add(
                PipelineDestination(
                    pipeline_id=pipeline.id,
                    destination_id=target.id,
                    connector_id=connector.id,
                    name="Archive " + prefix,
                    delivery_type="OBJECT_STORAGE",
                    topic_mapping_json=[{"topic": prefix + ".public.customers"}],
                )
            )
        await db.commit()


async def test_resource_list_query_counts_do_not_grow_per_row(client, db_factory):
    engine = db_factory.kw["bind"].sync_engine
    queries = []

    def count(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        await add_resources(db_factory, 1)
        paths = [
            "/connections",
            "/pipelines",
            "/deliveries",
            "/destinations",
            "/connect/connectors",
            "/monitoring/overview",
        ]
        baseline = {}
        for path in paths:
            queries.clear()
            response = await client.get("/api/v1" + path)
            assert response.status_code == 200, response.text
            baseline[path] = len(queries)
        await add_resources(db_factory, 8)
        for path in paths:
            queries.clear()
            response = await client.get("/api/v1" + path)
            assert response.status_code == 200, response.text
            assert len(queries) == baseline[path], (
                f"{path}: queries grew from {baseline[path]} to {len(queries)}"
            )
    finally:
        event.remove(engine, "before_cursor_execute", count)
