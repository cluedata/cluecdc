import re

from app.core.errors import DomainError


def iceberg_type(source_type: str) -> str:
    value = source_type.lower().strip()
    value = re.sub(r"\s+", " ", value)
    if value in {"boolean", "bool"}:
        return "boolean"
    if value in {"smallint", "int2", "smallserial"}:
        return "int"
    if value in {"integer", "int", "int4", "serial", "mediumint", "tinyint"}:
        return "int"
    if value in {"bigint", "int8", "bigserial"}:
        return "long"
    if value in {"real", "float4", "float"}:
        return "float"
    if value in {"double precision", "float8", "double"}:
        return "double"
    decimal = re.fullmatch(r"(?:numeric|decimal)\((\d+),(\d+)\)", value)
    if decimal:
        precision, scale = map(int, decimal.groups())
        if precision <= 38 and scale <= precision:
            return f"decimal({precision},{scale})"
    if value in {"numeric", "decimal"}:
        raise DomainError(
            "ICEBERG_TYPE_MAPPING_REQUIRED",
            "Unbounded decimal requires an explicit precision and scale",
            422,
        )
    if re.fullmatch(r"(?:character varying|varchar|character|char)\(\d+\)", value) or value in {
        "varchar",
        "text",
        "char",
        "character varying",
        "character",
    }:
        return "string"
    if value == "date":
        return "date"
    if value.startswith("time") and "zone" not in value:
        return "time"
    if (
        value in {"timestamp", "timestamp without time zone", "datetime"}
        or value.startswith("timestamp(")
        and "zone" not in value
    ):
        return "timestamp"
    if value in {"timestamp with time zone", "timestamptz"}:
        return "timestamptz"
    if value == "uuid":
        return "uuid"
    if value in {"bytea", "binary", "varbinary", "blob"} or value.startswith(
        ("binary(", "varbinary(")
    ):
        return "binary"
    if value in {"json", "jsonb"}:
        # The Kafka Connect representation is a string unless an explicit struct schema exists.
        return "string"
    raise DomainError(
        "ICEBERG_TYPE_UNSUPPORTED",
        f"Source type '{source_type}' has no safe Iceberg mapping",
        422,
        {"source_type": source_type},
    )


def map_columns(columns: list[dict]) -> list[dict]:
    return [
        {
            "name": column["name"],
            "source_type": column["type"],
            "iceberg_type": iceberg_type(column["type"]),
            "nullable": bool(column.get("nullable", True)),
        }
        for column in columns
    ]
