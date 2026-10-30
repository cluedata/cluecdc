import json

import httpx

from app.core.errors import DomainError
from app.models.entities import Connection
from app.schemas.requests import DeliveryInput
from app.services.warehouses.schema import validate_fixed_history_schema


def _quote(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


HISTORY_COLUMN_TYPES = {
    "_clue_topic": "String",
    "_clue_partition": "Int32",
    "_clue_offset": "Int64",
    "_clue_timestamp": "Nullable(Int64)",
    "_clue_operation": "LowCardinality(String)",
    "_clue_key": "Nullable(String)",
    "_clue_before": "Nullable(String)",
    "_clue_after": "Nullable(String)",
    "_clue_source": "Nullable(String)",
}


class ClickHouseDestinationAdapter:
    """Provision the fixed append-only history schema expected by the sink SMT."""

    def __init__(self, connection: Connection, password: str):
        self.connection = connection
        self.password = password

    async def _query(self, statement: str) -> str:
        config = self.connection.config_json
        scheme = "https" if config.get("ssl_enabled", True) else "http"
        try:
            async with httpx.AsyncClient(timeout=20, verify=True) as client:
                response = await client.post(
                    f"{scheme}://{config['host']}:{config['port']}/",
                    params={"database": str(config["database_name"])},
                    content=statement,
                    auth=(str(config["username"]), self.password),
                    headers={"Content-Type": "text/plain; charset=utf-8"},
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DomainError(
                "CLICKHOUSE_UNAVAILABLE", "ClickHouse HTTP endpoint could not be reached", 503
            ) from exc
        if response.is_error:
            raise DomainError(
                "CLICKHOUSE_QUERY_FAILED",
                "ClickHouse rejected destination table validation or preparation",
                422,
                {"status_code": response.status_code},
            )
        return response.text

    async def test_connection(self) -> dict:
        return await test_connection(self.connection, {"password": self.password})

    async def validate(self, data: DeliveryInput, metadata: dict) -> None:
        database = str(self.connection.config_json["database_name"])
        for mapping in data.mappings:
            if mapping.schema_name != database:
                raise DomainError(
                    "CLICKHOUSE_DATABASE_MISMATCH",
                    "ClickHouse mappings must use the configured database",
                    422,
                    {"database": database, "mapping_database": mapping.schema_name},
                )
            exists = (
                await self._query(
                    f"EXISTS TABLE {_quote(database)}.{_quote(mapping.table_name)} "
                    "FORMAT TabSeparated"
                )
            ).strip()
            if exists != "1" and not data.auto_create:
                raise DomainError(
                    "DESTINATION_TABLE_MISSING",
                    "ClickHouse target table does not exist and auto create is disabled",
                    422,
                    {"table": f"{database}.{mapping.table_name}"},
                )
            if exists == "1":
                try:
                    description = json.loads(
                        await self._query(
                            f"DESCRIBE TABLE {_quote(database)}.{_quote(mapping.table_name)} "
                            "FORMAT JSON"
                        )
                    )
                    actual = {row["name"]: row["type"] for row in description["data"]}
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise DomainError(
                        "CLICKHOUSE_TABLE_SCHEMA_INVALID",
                        "ClickHouse returned an invalid target table description",
                        503,
                    ) from exc
                validate_fixed_history_schema(
                    actual,
                    HISTORY_COLUMN_TYPES,
                    provider="CLICKHOUSE",
                    table=f"{database}.{mapping.table_name}",
                )

    async def prepare(self, data: DeliveryInput, metadata: dict) -> None:
        if not data.auto_create:
            return
        database = str(self.connection.config_json["database_name"])
        columns = ", ".join(
            f"{_quote(name)} {column_type}" for name, column_type in HISTORY_COLUMN_TYPES.items()
        )
        for mapping in data.mappings:
            await self._query(
                f"CREATE TABLE IF NOT EXISTS {_quote(database)}.{_quote(mapping.table_name)} "
                f"({columns}) ENGINE = MergeTree ORDER BY "
                "(`_clue_topic`, `_clue_partition`, `_clue_offset`)"
            )

    async def drop_table(self, schema_name: str, table_name: str) -> None:
        database = str(self.connection.config_json["database_name"])
        if schema_name != database:
            raise DomainError("CLICKHOUSE_DATABASE_MISMATCH", "Unexpected ClickHouse database", 422)
        await self._query(f"DROP TABLE IF EXISTS {_quote(database)}.{_quote(table_name)}")


async def test_connection(connection: Connection, credentials: dict[str, str]) -> dict:
    config = connection.config_json
    scheme = "https" if config.get("ssl_enabled", True) else "http"
    url = f"{scheme}://{config['host']}:{config['port']}/"
    try:
        async with httpx.AsyncClient(timeout=15, verify=True) as client:
            response = await client.post(
                url,
                params={"database": str(config["database_name"])},
                content="SELECT version(), currentDatabase() FORMAT JSON",
                auth=(str(config["username"]), credentials["password"]),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise DomainError(
            "CLICKHOUSE_UNAVAILABLE", "ClickHouse HTTP endpoint could not be reached", 503
        ) from exc
    if response.status_code in {401, 403}:
        raise DomainError(
            "CLICKHOUSE_AUTH_FAILED", "ClickHouse rejected the supplied credentials", 422
        )
    if response.is_error:
        raise DomainError(
            "CLICKHOUSE_CONNECTION_FAILED",
            "ClickHouse rejected the connection validation query",
            422,
            {"status_code": response.status_code},
        )
    try:
        row = response.json()["data"][0]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DomainError(
            "CLICKHOUSE_RESPONSE_INVALID", "ClickHouse returned an invalid validation response", 503
        ) from exc
    if row.get("currentDatabase()") != config["database_name"]:
        raise DomainError(
            "CLICKHOUSE_DATABASE_MISMATCH", "Connected to an unexpected ClickHouse database", 422
        )
    return {
        "success": True,
        "status": "HEALTHY",
        "checks": [
            {"name": "http_connectivity", "status": "success"},
            {"name": "authentication", "status": "success"},
            {"name": "database_access", "status": "success"},
            {
                "name": "server_version",
                "status": "success",
                "message": str(row.get("version()", "unknown")),
            },
        ],
    }
