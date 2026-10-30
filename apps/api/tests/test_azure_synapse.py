import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.models.entities import AzureSynapseBatchLedger, Connection
from app.providers.types import destination_type, parse_type
from app.schemas.connections import ConnectionInput
from app.services.azure_synapse import connection as connection_test
from app.services.azure_synapse.ledger import checkpoint_ready, fail, transition
from app.services.azure_synapse.planner import (
    append_rows,
    classify_error,
    plan_batch,
    reduce_stateful,
)
from app.services.azure_synapse.runtime import _credential_sql
from app.services.azure_synapse.sql import (
    Column,
    append_apply_sql,
    copy_into_sql,
    create_table_sql,
    stateful_apply_sql,
)


def payload() -> dict:
    return {
        "name": "Synapse warehouse",
        "category": "DATABASE",
        "provider": "AZURE_SYNAPSE",
        "capabilities": ["DESTINATION"],
        "config": {
            "workspace_name": "analytics",
            "sql_endpoint": "analytics.sql.azuresynapse.net",
            "port": 1433,
            "database_name": "warehouse",
            "target_schema": "dbo",
            "authentication": "SQL_PASSWORD",
            "username": "cluecdc_loader",
            "ssl_enabled": True,
            "storage_account": "cluecdcstage",
            "storage_type": "ADLS_GEN2",
            "container": "stage",
            "staging_prefix": "cluecdc/synapse",
            "storage_authentication": "ACCOUNT_KEY",
            "distribution_strategy": "ROUND_ROBIN",
        },
        "credentials": {
            "password": "sql-private-password",
            "storage_account_key": "storage-private-key",
        },
    }


async def test_synapse_connection_crud_masks_both_secret_classes(client, db_factory):
    response = await client.post("/api/v1/connections", json=payload())
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["provider"] == "AZURE_SYNAPSE"
    assert value["capabilities"] == ["DESTINATION"]
    assert "sql-private-password" not in response.text
    assert "storage-private-key" not in response.text

    source_list = await client.get("/api/v1/connections?capability=SOURCE")
    destination_list = await client.get("/api/v1/connections?capability=DESTINATION")
    assert source_list.json() == []
    assert [item["id"] for item in destination_list.json()] == [value["id"]]


def test_synapse_rejects_serverless_insecure_and_irrelevant_auth_fields():
    data = payload()
    data["config"]["sql_endpoint"] = "analytics-ondemand.sql.azuresynapse.net"
    with pytest.raises(ValidationError, match="Serverless SQL Pool"):
        ConnectionInput.model_validate(data)

    data = payload()
    data["config"]["ssl_enabled"] = False
    with pytest.raises(ValidationError, match="require TLS"):
        ConnectionInput.model_validate(data)

    data = payload()
    data["config"]["tenant_id"] = "not-applicable"
    with pytest.raises(ValidationError, match="apply only"):
        ConnectionInput.model_validate(data)


def test_synapse_type_mapping_preserves_precision_and_rejects_unsafe_decimal():
    mapped = destination_type(parse_type("postgresql", "numeric(38,12)"), "azure_synapse")
    assert mapped.destination_type == "decimal(38,12)"
    assert mapped.compatibility == "COMPATIBLE"
    incompatible = destination_type(parse_type("postgresql", "numeric(39,12)"), "azure_synapse")
    assert incompatible.compatibility == "INCOMPATIBLE"
    assert incompatible.destination_type is None


def test_synapse_sql_generation_quotes_identifiers_and_gates_stateful_cdc():
    ddl = create_table_sql(
        "dbo",
        "order]events",
        [Column("id", "bigint", False), Column("payload", "nvarchar(max)")],
        "HASH",
        "id",
    )
    assert "[order]]events]" in ddl
    assert "DISTRIBUTION = HASH([id])" in ddl
    assert "PRIMARY KEY" not in ddl
    copy = copy_into_sql(
        "dbo", "_clue_stage", "https://acct.blob.core.windows.net/stage/batch/file.csv"
    )
    assert copy.startswith("COPY INTO [dbo].[_clue_stage]")
    assert "MAXERRORS = 0" in copy
    with pytest.raises(DomainError):
        copy_into_sql(
            "dbo",
            "_clue_stage",
            "https://acct.blob.core.windows.net/stage/file.csv'; DROP TABLE users;--",
        )
    append = append_apply_sql("dbo", "events", "_stage", ["id", "payload"])
    assert append == (
        "INSERT INTO [dbo].[events] ([id], [payload]) SELECT [id], [payload] FROM [dbo].[_stage];"
    )
    with pytest.raises(DomainError) as error:
        stateful_apply_sql()
    assert error.value.code == "AZURE_SYNAPSE_STATEFUL_NOT_VERIFIED"


def test_synapse_batch_ledger_never_checkpoints_before_final_apply():
    batch = AzureSynapseBatchLedger(
        batch_id="batch-1",
        source_offsets_json={"topic:0": {"first": 10, "last": 20}},
        staging_manifest_json={},
        status="CREATED",
    )
    for status in ("STAGING", "STAGED", "APPLYING"):
        transition(batch, status)
        assert not checkpoint_ready(batch)
    transition(batch, "APPLIED")
    assert checkpoint_ready(batch)
    with pytest.raises(DomainError):
        fail(batch, "COPY_FAILED")
    transition(batch, "CHECKPOINTED")
    assert batch.checkpointed_at is not None


def test_synapse_batch_ledger_distinguishes_retryable_and_terminal_failures():
    retryable = AzureSynapseBatchLedger(
        batch_id="batch-retry",
        source_offsets_json={},
        staging_manifest_json={},
        status="STAGING",
    )
    fail(retryable, "AZURE_SYNAPSE_TRANSIENT", retryable=True)
    assert retryable.status == "RETRYABLE_FAILED"
    transition(retryable, "STAGING")

    terminal = AzureSynapseBatchLedger(
        batch_id="batch-terminal",
        source_offsets_json={},
        staging_manifest_json={},
        status="APPLYING",
    )
    fail(terminal, "AZURE_SYNAPSE_PERMISSION_DENIED", retryable=False)
    assert terminal.status == "TERMINAL_FAILED"
    with pytest.raises(DomainError):
        transition(terminal, "APPLYING")


async def test_connection_test_reports_storage_failure_separately(monkeypatch):
    target = Connection(
        name="Synapse",
        category="DATABASE",
        provider="AZURE_SYNAPSE",
        capabilities_json=["DESTINATION"],
        config_json=ConnectionInput.model_validate(payload()).config,
    )
    monkeypatch.setattr(
        connection_test,
        "_test_sql",
        lambda connection, credentials: [{"name": "sql_connectivity", "status": "success"}],
    )

    def storage_failure(connection, credentials):
        raise DomainError("AZURE_STORAGE_PERMISSION_DENIED", "Azure Storage permission failed", 422)

    monkeypatch.setattr(connection_test, "_test_storage", storage_failure)
    with pytest.raises(DomainError) as error:
        await connection_test.test_connection(target, {})
    assert error.value.code == "AZURE_STORAGE_PERMISSION_DENIED"


def test_batch_planning_append_metadata_and_idempotent_identity():
    events = [
        {
            "topic": "capture.public.orders",
            "partition": 0,
            "offset": 12,
            "timestamp": 2,
            "operation": "c",
            "key": None,
            "before": None,
            "after": {"description": "keyless event"},
            "source": {"lsn": 99},
        },
        {
            "topic": "capture.public.orders",
            "partition": 0,
            "offset": 11,
            "timestamp": 1,
            "operation": "r",
            "key": None,
            "before": None,
            "after": {"description": "snapshot"},
            "source": {"snapshot": True},
        },
    ]
    first = plan_batch("delivery-1", events, "cluecdc/synapse")
    replay = plan_batch("delivery-1", list(reversed(events)), "cluecdc/synapse")
    assert first.batch_id == replay.batch_id
    assert first.source_offsets == {"capture.public.orders:0": {"first": 11, "last": 12}}
    rows = append_rows(events)
    assert [row["_clue_offset"] for row in rows] == [11, 12]
    assert all(row["_clue_key"] is None for row in rows)


def test_stateful_reduction_handles_composite_key_change_and_latest_delete():
    events = [
        {
            "topic": "orders",
            "partition": 0,
            "offset": 20,
            "operation": "u",
            "before": {"tenant": 1, "id": 7, "value": "old"},
            "after": {"tenant": 1, "id": 8, "value": "new"},
        },
        {
            "topic": "orders",
            "partition": 0,
            "offset": 21,
            "operation": "d",
            "before": {"tenant": 1, "id": 8, "value": "new"},
            "after": None,
        },
    ]
    mutations = reduce_stateful(events, ["tenant", "id"])
    assert [(item["key"], item["action"]) for item in mutations] == [
        ((1, 7), "DELETE"),
        ((1, 8), "DELETE"),
    ]
    assert all(item["offset"] >= 20 for item in mutations)


@pytest.mark.parametrize(
    "message,code,retryable",
    [
        ("42108 pool paused", "AZURE_SYNAPSE_POOL_PAUSED", True),
        ("40501 service busy", "AZURE_SYNAPSE_THROTTLED", True),
        ("403 permission denied", "AZURE_SYNAPSE_PERMISSION_DENIED", False),
    ],
)
def test_error_classification(message, code, retryable):
    assert classify_error(RuntimeError(message)) == {"code": code, "retryable": retryable}


def test_copy_service_principal_uses_v1_oauth_endpoint():
    connection = Connection(
        name="Synapse",
        category="DATABASE",
        provider="AZURE_SYNAPSE",
        capabilities_json=["DESTINATION"],
        config_json={
            "storage_authentication": "SERVICE_PRINCIPAL",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "client_id": "22222222-2222-2222-2222-222222222222",
        },
    )
    sql = _credential_sql(connection, {"client_secret": "secret'quoted"})
    assert "22222222-2222-2222-2222-222222222222@https://login.microsoftonline.com/" in sql
    assert "/11111111-1111-1111-1111-111111111111/oauth2/token" in sql
    assert "secret''quoted" in sql
