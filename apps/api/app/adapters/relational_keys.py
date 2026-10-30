"""Bounded, read-only custom-key validation shared by new relational adapters."""

import asyncio

from app.core.errors import DomainError


async def validate_custom_key(adapter, schema, table, keys, dialect):
    metadata = await adapter.inspect_table(schema, table)
    columns = {column["name"]: column for column in metadata.get("columns", [])}
    invalid = {"status": "INVALID", "enforced": False, "verified": False}
    if not keys or any(key not in columns for key in keys):
        return {**invalid, "errors": ["Selected key columns do not exist"]}
    if any(columns[key]["nullable"] for key in keys):
        return {**invalid, "errors": ["Key columns must be declared NOT NULL"]}
    candidate = next((index for index in metadata.get("indexes", [])
                      if index.get("unique") and index.get("valid", True)
                      and not index.get("partial") and not index.get("expression")
                      and index.get("columns") == keys), None)
    quote = adapter._quote
    qualified = f"{quote(schema)}.{quote(table)}"
    fields = ",".join(quote(key) for key in keys)
    condition = " OR ".join(f"{quote(key)} IS NULL" for key in keys)
    prefix = "SELECT TOP (1) 1 AS present" if dialect == "sqlserver" else "SELECT 1 AS present"
    suffix = " FETCH FIRST 1 ROWS ONLY" if dialect == "oracle" else " LIMIT 1" if dialect == "mariadb" else ""
    try:
        async with asyncio.timeout(10):
            async with adapter.connection() as connection:
                if dialect == "mariadb":
                    async with connection.cursor() as cursor:
                        await cursor.execute("SET SESSION max_statement_time=5")
                    one = adapter._one
                else:
                    one = adapter._one
                nulls = await one(connection, f"{prefix} FROM {qualified} WHERE {condition}{suffix}")
                duplicates = await one(connection,
                    f"{prefix} FROM {qualified} GROUP BY {fields} HAVING COUNT(*) > 1{suffix}")
    except TimeoutError:
        raise DomainError("CUSTOM_KEY_VALIDATION_TIMEOUT", "Key scan timed out", 422) from None
    if nulls or duplicates:
        return {**invalid, "errors": ["NULL or duplicate key values exist"]}
    return {"status": "DATABASE_ENFORCED" if candidate else "VERIFIED_ONLY",
            "enforced": bool(candidate), "verified": True, "errors": [],
            "constraint_name": candidate["name"] if candidate else None,
            "warning": None if candidate else "A scan does not guarantee future uniqueness",
            "suggested_sql": None}
