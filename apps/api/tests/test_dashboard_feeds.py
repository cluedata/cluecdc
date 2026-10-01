from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.models.entities import PipelineEvent, SchemaVersion


async def test_dashboard_feeds_are_bounded_and_prioritize_active_errors(
    client, db_factory, source_payload
):
    source = (await client.post("/api/v1/sources", json=source_payload)).json()
    now = datetime.now(UTC)
    async with db_factory() as db:
        for i, status in enumerate(["OPEN", "ACKNOWLEDGED", "RESOLVED"]):
            db.add(
                PipelineEvent(
                    category="CONNECTOR",
                    severity="ERROR",
                    message=status,
                    status=status,
                    created_at=now + timedelta(seconds=i),
                )
            )
        for version in [1, 2, 3]:
            db.add(
                SchemaVersion(
                    source_id=UUID(source["id"]),
                    schema_name="public",
                    table_name="customers",
                    version=version,
                    schema_hash=str(version) * 64,
                    schema_json={},
                    diff_json=[],
                    created_at=now + timedelta(seconds=version),
                )
            )
        await db.commit()
    errors = (await client.get("/api/v1/operations/errors?limit=2&active=true")).json()
    assert [error["status"] for error in errors] == ["OPEN", "ACKNOWLEDGED"]
    assert len((await client.get("/api/v1/operations/errors?limit=1")).json()) == 1
    changes = (await client.get("/api/v1/data/schemas?limit=1&changes_only=true")).json()
    assert [change["version"] for change in changes] == [3]
    assert len((await client.get("/api/v1/data/schemas")).json()) == 3
    assert (await client.get("/api/v1/data/schemas?limit=501")).status_code == 422
