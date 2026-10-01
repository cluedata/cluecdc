from dataclasses import dataclass

from app.adapters.postgres import PostgresAdapter
from app.core.errors import DomainError


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


@dataclass(frozen=True)
class PublicationTable:
    schema_name: str
    table_name: str


class PostgresPublicationManager:
    """Owns safe, idempotent publication membership changes."""

    def __init__(self, database: PostgresAdapter, publication_name: str):
        self.database = database
        self.publication_name = publication_name

    async def inspect_publication(self) -> dict:
        async with self.database.connection() as connection:
            exists = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_publication WHERE pubname=$1)",
                self.publication_name,
            )
            tables = await self._list_tables(connection) if exists else []
            return {"exists": exists, "name": self.publication_name, "tables": tables}

    async def list_tables(self) -> list[dict]:
        async with self.database.connection() as connection:
            return await self._list_tables(connection)

    async def _list_tables(self, connection) -> list[dict]:
        rows = await connection.fetch(
            """
            SELECT schemaname, tablename
            FROM pg_publication_tables
            WHERE pubname=$1
            ORDER BY schemaname, tablename
            """,
            self.publication_name,
        )
        return [{"schema_name": row["schemaname"], "table_name": row["tablename"]} for row in rows]

    async def validate_permissions(self, tables: list[PublicationTable]) -> dict:
        async with self.database.connection() as connection:
            missing = []
            for table in tables:
                owns = await connection.fetchval(
                    """
                    SELECT pg_has_role(c.relowner, 'USAGE')
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=$1 AND c.relname=$2 AND c.relkind='r'
                    """,
                    table.schema_name,
                    table.table_name,
                )
                if not owns:
                    missing.append(f"{table.schema_name}.{table.table_name}")
            return {"valid": not missing, "missing_ownership": missing}

    async def add_table(self, schema_name: str, table_name: str) -> None:
        async with self.database.connection() as connection:
            async with connection.transaction():
                await self._change_table(connection, "ADD", schema_name, table_name)

    async def remove_table(self, schema_name: str, table_name: str) -> None:
        async with self.database.connection() as connection:
            async with connection.transaction():
                await self._change_table(connection, "DROP", schema_name, table_name)

    async def _change_table(
        self, connection, action: str, schema_name: str, table_name: str
    ) -> None:
        exists = await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_publication WHERE pubname=$1)",
            self.publication_name,
        )
        if not exists:
            raise DomainError(
                "PUBLICATION_NOT_FOUND",
                f"CDC publication {self.publication_name} does not exist",
                409,
                {"publication": self.publication_name},
            )
        present = await connection.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM pg_publication_tables
                WHERE pubname=$1 AND schemaname=$2 AND tablename=$3
            )
            """,
            self.publication_name,
            schema_name,
            table_name,
        )
        if (action == "ADD" and present) or (action == "DROP" and not present):
            return
        publication = quote_identifier(self.publication_name)
        table = f"{quote_identifier(schema_name)}.{quote_identifier(table_name)}"
        try:
            await connection.execute(f"ALTER PUBLICATION {publication} {action} TABLE {table}")
        except Exception as exc:
            raise DomainError(
                "PUBLICATION_UPDATE_FAILED",
                f"ClueCDC cannot {action.lower()} {schema_name}.{table_name} "
                f"{'to' if action == 'ADD' else 'from'} publication {self.publication_name}",
                422,
                {"publication": self.publication_name, "table": f"{schema_name}.{table_name}"},
            ) from exc
        final = await connection.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM pg_publication_tables
                WHERE pubname=$1 AND schemaname=$2 AND tablename=$3
            )
            """,
            self.publication_name,
            schema_name,
            table_name,
        )
        if (action == "ADD") != bool(final):
            raise DomainError(
                "PUBLICATION_VALIDATION_FAILED",
                "PostgreSQL publication membership did not match the requested state",
                502,
            )


class NoopPublicationManager:
    """Provider hook for CDC engines without PostgreSQL-style publications."""

    async def add_table(self, schema_name: str, table_name: str) -> None:
        return None

    async def remove_table(self, schema_name: str, table_name: str) -> None:
        return None
