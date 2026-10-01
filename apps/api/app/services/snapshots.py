import json
import time
import uuid

from app.adapters.mysql import MySQLAdapter
from app.adapters.postgres import PostgresAdapter
from app.services.debezium import signal_table_name
from app.services.publication import quote_identifier


class DebeziumSignalService:
    """Hides source signaling SQL from API and UI layers."""

    def __init__(self, database: PostgresAdapter, topic_prefix: str):
        self.database = database
        self.topic_prefix = topic_prefix
        self.table_name = signal_table_name(topic_prefix)

    async def ensure_table(self) -> str:
        table = quote_identifier(self.table_name)
        async with self.database.connection() as connection:
            await connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS public.{table} (
                    id varchar(128) PRIMARY KEY,
                    type varchar(32) NOT NULL,
                    data varchar(4096)
                )
                """
            )
        return f"public.{self.table_name}"

    async def execute_incremental_snapshot(
        self, pipeline_id: uuid.UUID, schema_name: str, table_name: str
    ) -> str:
        signal_id = self._id(pipeline_id, table_name, "snapshot")
        await self._send(
            signal_id,
            "execute-snapshot",
            {
                "data-collections": [f"{schema_name}.{table_name}"],
                "type": "incremental",
            },
        )
        return signal_id

    async def stop_snapshot(self, pipeline_id: uuid.UUID, schema_name: str, table_name: str) -> str:
        signal_id = self._id(pipeline_id, table_name, "stop")
        await self._send(
            signal_id,
            "stop-snapshot",
            {
                "data-collections": [f"{schema_name}.{table_name}"],
                "type": "incremental",
            },
        )
        return signal_id

    async def _send(self, signal_id: str, signal_type: str, data: dict) -> None:
        table = quote_identifier(self.table_name)
        async with self.database.connection() as connection:
            await connection.execute(
                f"INSERT INTO public.{table} (id, type, data) VALUES ($1, $2, $3)",
                signal_id,
                signal_type,
                json.dumps(data, separators=(",", ":")),
            )

    def _id(self, pipeline_id: uuid.UUID, table_name: str, action: str) -> str:
        safe_table = "".join(character for character in table_name if character.isalnum())[:20]
        return f"cluecdc_{action}_{pipeline_id.hex[:8]}_{safe_table}_{time.time_ns()}"


class MySQLDebeziumSignalService:
    def __init__(self, database: MySQLAdapter, topic_prefix: str, database_name: str):
        self.database = database
        self.topic_prefix = topic_prefix
        self.database_name = database_name
        self.table_name = signal_table_name(topic_prefix)

    @staticmethod
    def _quote(value: str) -> str:
        return "`" + value.replace("`", "``") + "`"

    async def ensure_table(self) -> str:
        qualified = f"{self._quote(self.database_name)}.{self._quote(self.table_name)}"
        async with self.database.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"CREATE TABLE IF NOT EXISTS {qualified} ("
                    "id varchar(128) PRIMARY KEY, type varchar(32) NOT NULL, data text)"
                )
        return f"{self.database_name}.{self.table_name}"

    async def execute_incremental_snapshot(
        self, pipeline_id: uuid.UUID, schema_name: str, table_name: str
    ) -> str:
        return await self._action(
            pipeline_id, schema_name, table_name, "snapshot", "execute-snapshot"
        )

    async def stop_snapshot(self, pipeline_id: uuid.UUID, schema_name: str, table_name: str) -> str:
        return await self._action(pipeline_id, schema_name, table_name, "stop", "stop-snapshot")

    async def _action(self, pipeline_id, schema_name, table_name, action, signal_type) -> str:
        safe_table = "".join(character for character in table_name if character.isalnum())[:20]
        signal_id = f"cluecdc_{action}_{pipeline_id.hex[:8]}_{safe_table}_{time.time_ns()}"
        qualified = f"{self._quote(self.database_name)}.{self._quote(self.table_name)}"
        data = json.dumps(
            {"data-collections": [f"{schema_name}.{table_name}"], "type": "incremental"},
            separators=(",", ":"),
        )
        async with self.database.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"INSERT INTO {qualified} (id, type, data) VALUES (%s, %s, %s)",
                    (signal_id, signal_type, data),
                )
        return signal_id
