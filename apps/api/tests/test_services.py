from uuid import UUID

import httpx
from sqlalchemy import select

from app.models.entities import AuditLog, Connector, Job, SchemaVersion, SourceTable
from app.services.source import discover, schema_diff
from app.workers import worker
from tests.test_api import setup_pipeline


def _schema(columns, primary_key=None):
    return {"columns": columns, "primary_key": primary_key or ["id"]}


def test_schema_diff_compatibility_matrix():
    base = [
        {"name": "id", "type": "bigint", "nullable": False},
        {"name": "name", "type": "character varying(32)", "nullable": False},
    ]
    changes = schema_diff(
        _schema(base),
        _schema(
            [
                base[0],
                {**base[1], "type": "character varying(64)"},
                {"name": "note", "type": "text", "nullable": True},
                {
                    "name": "status",
                    "type": "text",
                    "nullable": False,
                    "has_default": True,
                },
            ]
        ),
        "postgresql",
    )
    assert {(item["change"], item.get("column"), item["classification"]) for item in changes} == {
        ("type_changed", "name", "NON_BREAKING"),
        ("column_added", "note", "NON_BREAKING"),
        ("column_added", "status", "NON_BREAKING"),
    }


def test_schema_diff_marks_drop_narrowing_type_and_primary_key_as_breaking():
    changes = schema_diff(
        _schema(
            [
                {"name": "id", "type": "bigint", "nullable": False},
                {"name": "name", "type": "character varying(64)", "nullable": True},
                {"name": "obsolete", "type": "text", "nullable": True},
            ]
        ),
        _schema(
            [
                {"name": "id", "type": "text", "nullable": False},
                {"name": "name", "type": "character varying(16)", "nullable": False},
            ],
            ["name"],
        ),
        "postgresql",
    )
    assert all(item["classification"] == "BREAKING" for item in changes)
    assert {item["change"] for item in changes} == {
        "column_removed",
        "type_changed",
        "nullable_changed",
        "primary_key_changed",
    }


def test_schema_diff_marks_required_column_without_default_for_review():
    changes = schema_diff(
        _schema([{"name": "id", "type": "bigint", "nullable": False}]),
        _schema(
            [
                {"name": "id", "type": "bigint", "nullable": False},
                {"name": "required", "type": "text", "nullable": False},
            ]
        ),
    )
    assert changes == [
        {
            "column": "required",
            "change": "column_added",
            "classification": "POTENTIALLY_BREAKING",
        }
    ]


async def test_discovery_versions_only_change_with_schema(
    client, db_factory, source_payload, monkeypatch
):
    source = (await client.post("/api/v1/sources", json=source_payload)).json()
    data = {
        "schema_name": "public",
        "table_name": "customers",
        "primary_key_columns": ["id"],
        "columns_json": [{"name": "id", "type": "bigint", "nullable": False, "ordinal": 1}],
        "indexes_json": [],
        "estimated_rows": None,
        "estimated_size_bytes": 8192,
        "cdc_ready": True,
        "cdc_status": "READY",
        "cdc_issues": [],
    }

    async def tables(self):
        return [data.copy()]

    monkeypatch.setattr("app.adapters.postgres.PostgresAdapter.discover", tables)
    async with db_factory() as db:
        await discover(db, UUID(source["id"]), "tester")
        await db.commit()
        await discover(db, UUID(source["id"]), "tester")
        await db.commit()
        assert len((await db.scalars(select(SchemaVersion))).all()) == 1
        data["columns_json"] = [
            *data["columns_json"],
            {"name": "email", "type": "text", "nullable": True, "ordinal": 2},
        ]
        await discover(db, UUID(source["id"]), "tester")
        await db.commit()
        versions = (await db.scalars(select(SchemaVersion).order_by(SchemaVersion.version))).all()
        assert len(versions) == 2
        assert versions[1].diff_json[0]["classification"] == "NON_BREAKING"
        assert len((await db.scalars(select(SourceTable))).all()) == 1


async def test_durable_worker_completes_a_queued_job(
    client, db_factory, source_payload, monkeypatch
):
    source = (await client.post("/api/v1/sources", json=source_payload)).json()

    async def health(self):
        return {"status": "passed", "checks": []}

    monkeypatch.setattr("app.adapters.postgres.PostgresAdapter.readiness", health)
    monkeypatch.setattr(worker, "Session", db_factory)
    job = (await client.post(f"/api/v1/sources/{source['id']}/health")).json()
    assert job["status"] == "PENDING"
    assert await worker.run_job()
    response = (await client.get(f"/api/v1/jobs/{job['id']}")).json()
    assert response["status"] == "COMPLETED"
    async with db_factory() as db:
        logs = (await db.scalars(select(AuditLog))).all()
        assert "job.completed" in {log.action for log in logs}
        assert (await db.get(Job, UUID(job["id"]))).status == "COMPLETED"


async def test_deployment_compensates_if_metadata_persistence_fails(
    client, db_factory, source_payload, respx_mock, monkeypatch
):
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import AsyncSession

    monkeypatch.setattr(
        "app.adapters.kafka.KafkaExplorer.ensure_topics",
        AsyncMock(return_value={"topics": ["capture.public.customers"], "created": []}),
    )

    data = await setup_pipeline(client, db_factory, source_payload)
    respx_mock.put(
        "http://connect:8083/connector-plugins/io.debezium.connector.postgresql.PostgresConnector/config/validate"
    ).mock(return_value=httpx.Response(200, json={"error_count": 0}))
    p = (await client.post("/api/v1/pipelines", json=data)).json()
    name = "cluecdc-source-" + p["id"].replace("-", "")
    respx_mock.post("http://connect:8083/connectors").mock(
        return_value=httpx.Response(201, json={"name": name})
    )
    deletion = respx_mock.delete(f"http://connect:8083/connectors/{name}").mock(
        return_value=httpx.Response(204)
    )
    original = AsyncSession.flush

    async def failing_flush(self, *args, **kwargs):
        if any(isinstance(entity, Connector) for entity in self.new):
            raise RuntimeError("Simulated metadata persistence failure")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", failing_flush)
    assert (await client.post(f"/api/v1/pipelines/{p['id']}/deploy")).status_code == 500
    assert deletion.called
    assert (await client.get(f"/api/v1/pipelines/{p['id']}")).json()["connector_id"] is None
