from typing import Protocol

from app.adapters.mysql import MySQLAdapter
from app.adapters.postgres import PostgresAdapter
from app.core.errors import DomainError
from app.models.entities import Destination
from app.providers.types import destination_type, parse_type
from app.schemas.requests import DeliveryInput


class DestinationAdapter(Protocol):
    async def test_connection(self) -> dict: ...
    async def validate(self, data: DeliveryInput, metadata: dict) -> None: ...
    async def prepare(self, data: DeliveryInput, metadata: dict) -> None: ...
    async def drop_table(self, schema_name: str, table_name: str) -> None: ...


def supported_type(value: str) -> bool:
    """Backward-compatible PostgreSQL check backed by the logical type registry."""
    return destination_type(parse_type("postgresql", value), "postgresql").compatibility != (
        "INCOMPATIBLE"
    )


class PostgresDestinationAdapter:
    def __init__(self, destination: Destination, password: str):
        # The source adapter provides safe connection handling without logging credentials.
        self.database = PostgresAdapter(destination, password)  # type: ignore[arg-type]

    async def test_connection(self) -> dict:
        try:
            return await self.database.test()
        except DomainError as error:
            if error.code == "SOURCE_AUTH_FAILED":
                raise DomainError(
                    "DESTINATION_AUTH_FAILED", "Destination rejected the supplied credentials", 422
                ) from error
            raise DomainError(
                "DESTINATION_UNAVAILABLE", "Destination database connection or query failed", 503
            ) from error

    async def validate(self, data: DeliveryInput, metadata: dict) -> None:
        async with self.database.connection() as connection:
            for mapping in data.mappings:
                schema = await connection.fetchrow(
                    "SELECT oid FROM pg_namespace WHERE nspname=$1", mapping.schema_name
                )
                if not schema:
                    raise DomainError(
                        "DESTINATION_SCHEMA_MISSING",
                        "Create the destination schema before deployment",
                        422,
                        {"schema": mapping.schema_name},
                    )
                if not await connection.fetchval(
                    "SELECT has_schema_privilege($1::oid, 'USAGE')", schema["oid"]
                ):
                    raise DomainError(
                        "DESTINATION_PERMISSION_DENIED",
                        "Destination schema USAGE permission is required",
                        422,
                    )
                table = await connection.fetchrow(
                    "SELECT c.oid, pg_has_role(c.relowner, 'USAGE') AS owns_table FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=$1 AND c.relname=$2 AND c.relkind='r'",
                    mapping.schema_name,
                    mapping.table_name,
                )
                if not table:
                    if not data.auto_create:
                        raise DomainError(
                            "DESTINATION_TABLE_MISSING",
                            "Destination table does not exist and auto create is disabled",
                            422,
                            {"table": f"{mapping.schema_name}.{mapping.table_name}"},
                        )
                    if not await connection.fetchval(
                        "SELECT has_schema_privilege($1::oid, 'CREATE')", schema["oid"]
                    ):
                        raise DomainError(
                            "DESTINATION_PERMISSION_DENIED",
                            "Auto create requires destination schema CREATE permission",
                            422,
                        )
                    continue
                privileges = "INSERT,UPDATE,DELETE" if data.delete_enabled else "INSERT,UPDATE"
                if not await connection.fetchval(
                    "SELECT has_table_privilege($1::oid, $2)", table["oid"], privileges
                ):
                    # has_table_privilege with a comma list tests ANY; test each privilege below.
                    raise DomainError(
                        "DESTINATION_PERMISSION_DENIED",
                        "Destination write permission is required",
                        422,
                    )
                for privilege in privileges.split(","):
                    if not await connection.fetchval(
                        "SELECT has_table_privilege($1::oid, $2)", table["oid"], privilege
                    ):
                        raise DomainError(
                            "DESTINATION_PERMISSION_DENIED",
                            f"Destination {privilege} permission is required",
                            422,
                        )
                if data.auto_evolve and not table["owns_table"]:
                    raise DomainError(
                        "DESTINATION_PERMISSION_DENIED",
                        "Auto evolve requires destination table ownership",
                        422,
                    )
                keys = await connection.fetch(
                    "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
                    "ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
                    "WHERE i.indrelid=$1 AND i.indisprimary",
                    table["oid"],
                )
                if set(row["attname"] for row in keys) != set(
                    metadata[mapping.topic]["primary_keys"]
                ):
                    raise DomainError(
                        "DESTINATION_PRIMARY_KEY_MISMATCH",
                        "Destination primary key must match the selected topic's record key",
                        422,
                        {"table": f"{mapping.schema_name}.{mapping.table_name}"},
                    )
                columns = await connection.fetch(
                    "SELECT attname FROM pg_attribute "
                    "WHERE attrelid=$1 AND attnum>0 AND NOT attisdropped",
                    table["oid"],
                )
                missing = set(column["name"] for column in metadata[mapping.topic]["columns"]) - {
                    row["attname"] for row in columns
                }
                if missing and not data.auto_evolve:
                    raise DomainError(
                        "DESTINATION_SCHEMA_MISMATCH",
                        "Destination columns are missing and auto evolve is disabled",
                        422,
                        {"columns": sorted(missing)},
                    )

    async def prepare(self, data: DeliveryInput, metadata: dict) -> None:
        if not data.auto_create or data.auto_evolve:
            return
        # The JDBC plugin combines create/evolve. For create-only, provision once using
        # the validated source schema and let JDBC perform only DML afterward.
        async with self.database.connection() as connection:
            async with connection.transaction():
                for mapping in data.mappings:
                    table = metadata[mapping.topic]

                    def quote(value: str) -> str:
                        return '"' + value.replace('"', '""') + '"'

                    fields = [
                        f"{quote(column['name'])} {column['type']}"
                        + (" NOT NULL" if not column["nullable"] else "")
                        for column in table["columns"]
                    ]
                    fields.append(
                        "PRIMARY KEY ("
                        + ",".join(quote(key) for key in table["primary_keys"])
                        + ")"
                    )
                    await connection.execute(
                        f"CREATE TABLE IF NOT EXISTS {quote(mapping.schema_name)}."
                        f"{quote(mapping.table_name)} (" + ",".join(fields) + ")"
                    )

    async def drop_table(self, schema_name: str, table_name: str) -> None:
        def quote(value: str) -> str:
            return '"' + value.replace('"', '""') + '"'

        async with self.database.connection() as connection:
            await connection.execute(
                f"DROP TABLE IF EXISTS {quote(schema_name)}.{quote(table_name)}"
            )


class MySQLDestinationAdapter:
    def __init__(self, destination: Destination, password: str):
        self.database = MySQLAdapter(destination, password, role="destination")
        self.database_name = destination.database_name

    @staticmethod
    def quote(value: str) -> str:
        return "`" + value.replace("`", "``") + "`"

    async def test_connection(self) -> dict:
        async with self.database.connection() as connection:
            grants = await self.database._all(connection, "SHOW GRANTS FOR CURRENT_USER")
            text = " ".join(str(row[next(iter(row))]) for row in grants).upper()
            checks = [
                self.database._check("connection", True, True, True, "Check network settings"),
                self.database._check("authentication", True, True, True, "Verify credentials"),
            ]
            for privilege in ["CREATE", "ALTER", "INSERT", "UPDATE", "DELETE"]:
                passed = "ALL PRIVILEGES" in text or privilege in text
                checks.append(
                    self.database._check(
                        f"{privilege.lower()}_permission",
                        passed,
                        True,
                        passed,
                        f"Grant {privilege} on the destination database",
                    )
                )
            status = "failed" if any(check["status"] == "failed" for check in checks) else "passed"
            return {"status": status, "success": status == "passed", "checks": checks}

    async def validate(self, data: DeliveryInput, metadata: dict) -> None:
        invalid_namespace = next(
            (
                mapping.schema_name
                for mapping in data.mappings
                if mapping.schema_name != self.database_name
            ),
            None,
        )
        if invalid_namespace:
            raise DomainError(
                "DESTINATION_DATABASE_MISMATCH",
                "MySQL mappings must target the destination database",
                422,
                {"database": self.database_name, "mapping_database": invalid_namespace},
            )
        async with self.database.connection() as connection:
            for mapping in data.mappings:
                table = await self.database._one(
                    connection,
                    "SELECT COUNT(*) AS present FROM information_schema.tables "
                    "WHERE table_schema=%s AND table_name=%s",
                    (mapping.schema_name, mapping.table_name),
                )
                if not table or not table["present"]:
                    if not data.auto_create:
                        raise DomainError(
                            "DESTINATION_TABLE_MISSING",
                            "Destination table does not exist and auto create is disabled",
                            422,
                            {"table": f"{mapping.schema_name}.{mapping.table_name}"},
                        )
                    continue
                keys = await self.database._all(
                    connection,
                    "SELECT column_name AS name FROM information_schema.key_column_usage "
                    "WHERE table_schema=%s AND table_name=%s "
                    "AND constraint_name='PRIMARY' ORDER BY ordinal_position",
                    (mapping.schema_name, mapping.table_name),
                )
                if {row["name"] for row in keys} != set(metadata[mapping.topic]["primary_keys"]):
                    raise DomainError(
                        "DESTINATION_PRIMARY_KEY_MISMATCH",
                        "Destination primary key must match the selected topic's record key",
                        422,
                    )
                columns = await self.database._all(
                    connection,
                    "SELECT column_name AS name FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s",
                    (mapping.schema_name, mapping.table_name),
                )
                missing = {column["name"] for column in metadata[mapping.topic]["columns"]} - {
                    row["name"] for row in columns
                }
                if missing and not data.auto_evolve:
                    raise DomainError(
                        "DESTINATION_SCHEMA_MISMATCH",
                        "Destination columns are missing and auto evolve is disabled",
                        422,
                        {"columns": sorted(missing)},
                    )

    async def prepare(self, data: DeliveryInput, metadata: dict) -> None:
        if not data.auto_create or data.auto_evolve:
            return
        async with self.database.connection() as connection:
            async with connection.cursor() as cursor:
                for mapping in data.mappings:
                    table = metadata[mapping.topic]
                    fields = [
                        f"{self.quote(column['name'])} {column['type']}"
                        + (" NOT NULL" if not column["nullable"] else "")
                        for column in table["columns"]
                    ]
                    fields.append(
                        "PRIMARY KEY ("
                        + ",".join(self.quote(key) for key in table["primary_keys"])
                        + ")"
                    )
                    qualified = (
                        f"{self.quote(mapping.schema_name)}.{self.quote(mapping.table_name)}"
                    )
                    await cursor.execute(
                        f"CREATE TABLE IF NOT EXISTS {qualified} ("
                        + ",".join(fields)
                        + ") CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                    )

    async def drop_table(self, schema_name: str, table_name: str) -> None:
        async with self.database.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"DROP TABLE IF EXISTS {self.quote(schema_name)}.{self.quote(table_name)}"
                )
