import asyncio
import json

from app.core.errors import DomainError
from app.models.entities import Connection
from app.schemas.requests import DeliveryInput
from app.services.warehouses.schema import validate_fixed_history_schema

BIGQUERY_HISTORY_TYPES = {
    "_clue_topic": "STRING",
    "_clue_partition": "INTEGER",
    "_clue_offset": "INTEGER",
    "_clue_timestamp": "INTEGER",
    "_clue_operation": "STRING",
    "_clue_key": "STRING",
    "_clue_before": "STRING",
    "_clue_after": "STRING",
    "_clue_source": "STRING",
}


class BigQueryDestinationAdapter:
    def __init__(self, connection: Connection, credentials: dict[str, str]):
        self.connection = connection
        self.credentials = credentials

    async def test_connection(self) -> dict:
        return await test_connection(self.connection, self.credentials)

    async def validate(self, data: DeliveryInput, metadata: dict) -> None:
        dataset = str(self.connection.config_json["dataset_id"])
        if any(mapping.schema_name != dataset for mapping in data.mappings):
            raise DomainError(
                "BIGQUERY_DATASET_MISMATCH",
                "BigQuery mappings must use the configured dataset ID",
                422,
                {"dataset_id": dataset},
            )
        await asyncio.to_thread(self._validate_tables, data)

    def _validate_tables(self, data: DeliveryInput) -> None:
        from google.api_core.exceptions import NotFound

        config = self.connection.config_json
        client = _client(self.connection, self.credentials)
        try:
            for mapping in data.mappings:
                table_name = f"{config['project_id']}.{config['dataset_id']}.{mapping.table_name}"
                try:
                    table = client.get_table(table_name)
                except NotFound:
                    if data.auto_create:
                        continue
                    raise DomainError(
                        "DESTINATION_TABLE_MISSING",
                        "BigQuery history table does not exist and auto create is disabled",
                        422,
                        {"table": table_name},
                    ) from None
                actual = {field.name: field.field_type.upper() for field in table.schema}
                validate_fixed_history_schema(
                    actual,
                    BIGQUERY_HISTORY_TYPES,
                    provider="BIGQUERY",
                    table=table_name,
                )
        finally:
            client.close()

    async def prepare(self, data: DeliveryInput, metadata: dict) -> None:
        return None

    async def drop_table(self, schema_name: str, table_name: str) -> None:
        if schema_name != self.connection.config_json["dataset_id"]:
            raise DomainError("BIGQUERY_DATASET_MISMATCH", "Unexpected BigQuery dataset", 422)

        def drop() -> None:
            config = self.connection.config_json
            client = _client(self.connection, self.credentials)
            try:
                client.delete_table(
                    f"{config['project_id']}.{config['dataset_id']}.{table_name}",
                    not_found_ok=True,
                )
            finally:
                client.close()

        await asyncio.to_thread(drop)


def _client(connection: Connection, credentials: dict[str, str]):
    from google.cloud import bigquery

    config = connection.config_json
    google_credentials = None
    if config["authentication"] == "SERVICE_ACCOUNT":
        from google.oauth2 import service_account

        value = json.loads(credentials["service_account_json"])
        google_credentials = service_account.Credentials.from_service_account_info(value)
    return bigquery.Client(
        project=config["project_id"],
        credentials=google_credentials,
        location=config["dataset_location"],
    )


def _test(connection: Connection, credentials: dict[str, str]) -> dict:
    try:
        from google.cloud import bigquery

        config = connection.config_json
        client = _client(connection, credentials)
        dataset = client.get_dataset(f"{config['project_id']}.{config['dataset_id']}")
        if dataset.location.upper() != str(config["dataset_location"]).upper():
            raise DomainError(
                "BIGQUERY_LOCATION_MISMATCH",
                "The configured dataset location does not match BigQuery",
                422,
            )
        client.query(
            "SELECT 1",
            job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False),
        )
    except DomainError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DomainError(
            "BIGQUERY_CREDENTIALS_INVALID", "BigQuery credentials are invalid", 422
        ) from exc
    except Exception as exc:
        status = getattr(exc, "code", None)
        if status in {401, 403} or "permission" in str(exc).lower():
            raise DomainError(
                "BIGQUERY_PERMISSION_DENIED",
                "The BigQuery identity lacks dataset or query-job permissions",
                422,
            ) from exc
        if status == 404:
            raise DomainError("BIGQUERY_DATASET_NOT_FOUND", "BigQuery dataset was not found", 422)
        raise DomainError("BIGQUERY_UNAVAILABLE", "BigQuery validation failed", 503) from exc
    finally:
        if "client" in locals():
            client.close()
    return {
        "success": True,
        "status": "HEALTHY",
        "checks": [
            {"name": "authentication", "status": "success"},
            {"name": "dataset_access", "status": "success"},
            {"name": "dataset_location", "status": "success"},
            {"name": "query_job_permission", "status": "success"},
        ],
    }


async def test_connection(connection: Connection, credentials: dict[str, str]) -> dict:
    return await asyncio.to_thread(_test, connection, credentials)
