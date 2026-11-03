from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.core.errors import DomainError
from app.models.entities import Connector, Pipeline, PipelineDestination
from app.services.pipeline import reconcile
from tests.test_connector_recovery import recovery_entities


@pytest.mark.parametrize(
    "slot_status,expected",
    [("lost", "FAILED"), ("reserved", "RUNNING"), ("unavailable", "RUNNING")],
)
@pytest.mark.parametrize("connect_state", ["RUNNING", "UNASSIGNED"])
async def test_only_definitive_owned_slot_loss_overrides_connect_running(
    db_factory, monkeypatch, respx_mock, slot_status, expected, connect_state
):
    delivery_id, connector_id, _ = await recovery_entities(db_factory)
    observed = {
        "name": "sink-recovery",
        "connector": {"state": connect_state},
        "tasks": [{"id": 0, "state": "RUNNING"}] if connect_state == "RUNNING" else [],
    }
    respx_mock.get("http://connect:8083/connectors/sink-recovery/status").mock(
        return_value=httpx.Response(200, json=observed)
    )
    probe = AsyncMock(
        return_value={"slot_name": "owned_slot", "wal_status": slot_status, "restart_lsn": None}
    )
    if slot_status == "unavailable":
        probe.side_effect = DomainError("SOURCE_UNAVAILABLE", "Unavailable", 503)
    adapter = type("Adapter", (), {"replication_slot_status": probe})()
    monkeypatch.setattr("app.services.pipeline.source_adapter", AsyncMock(return_value=adapter))
    async with db_factory() as db:
        pipeline = await db.scalar(select(Pipeline))
        connector = await db.get(Connector, connector_id)
        connector.config_json = {"slot.name": "owned_slot"}
        pipeline.connector_id, pipeline.desired_state = connector_id, "RUNNING"
        target = await db.get(PipelineDestination, delivery_id)
        target.actual_state = "RUNNING"
        await db.commit()
        result = await reconcile(db, pipeline)
        assert result["actual_state"] == (
            "UNKNOWN" if connect_state == "UNASSIGNED" and expected == "RUNNING" else expected
        )
        assert result["connector"]["state"] == connect_state  # Keep actual Connect observation.
        assert pipeline.desired_state == "RUNNING"
        probe.assert_awaited_once_with("owned_slot")
        if slot_status == "lost":
            assert result["error"]["code"] == "SOURCE_RECOVERY_REQUIRED"
            assert result["error"]["retryable"] is False
        if slot_status == "unavailable":
            assert result["source_position"]["status"] == "UNAVAILABLE"


async def test_position_guard_respects_deliberate_pause(db_factory, monkeypatch, respx_mock):
    _, connector_id, _ = await recovery_entities(db_factory)
    respx_mock.get("http://connect:8083/connectors/sink-recovery/status").mock(
        return_value=httpx.Response(
            200, json={"connector": {"state": "PAUSED"}, "tasks": [{"id": 0, "state": "PAUSED"}]}
        )
    )
    factory = AsyncMock()
    monkeypatch.setattr("app.services.pipeline.source_adapter", factory)
    async with db_factory() as db:
        pipeline = await db.scalar(select(Pipeline))
        connector = await db.get(Connector, connector_id)
        connector.config_json = {"slot.name": "owned_slot"}
        pipeline.connector_id, pipeline.desired_state = connector_id, "PAUSED"
        await db.commit()
        result = await reconcile(db, pipeline)
        assert result["actual_state"] == "PAUSED" and pipeline.desired_state == "PAUSED"
        factory.assert_not_awaited()
