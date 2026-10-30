import re
from dataclasses import dataclass

from app.core.errors import DomainError
from app.providers.types import LogicalType


def quote_identifier(value: str) -> str:
    if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise DomainError("INVALID_SQL_IDENTIFIER", "SQL identifier is invalid", 422)
    return "[" + value.replace("]", "]]") + "]"


def map_type(logical: LogicalType) -> str:
    if logical.kind == "DECIMAL":
        precision = logical.precision or 38
        scale = logical.scale or 0
        if precision > 38 or scale > precision:
            raise DomainError(
                "AZURE_SYNAPSE_TYPE_INCOMPATIBLE",
                "Synapse decimal precision is limited to 38",
                422,
            )
        return f"decimal({precision},{scale})"
    mapping = {
        "BOOLEAN": "bit",
        "INT8": "smallint",
        "INT16": "smallint",
        "INT24": "int",
        "INT32": "bigint" if logical.unsigned else "int",
        "INT64": "decimal(20,0)" if logical.unsigned else "bigint",
        "FLOAT32": "real",
        "FLOAT64": "float",
        "TEXT": "nvarchar(max)",
        "TINYTEXT": "nvarchar(max)",
        "MEDIUMTEXT": "nvarchar(max)",
        "LONGTEXT": "nvarchar(max)",
        "BYTES": "varbinary(max)",
        "DATE": "date",
        "TIME": "time(6)",
        "DATETIME": "datetime2(6)",
        "TIMESTAMP": "datetime2(6)",
        "TIMESTAMPTZ": "datetimeoffset(6)",
        "YEAR": "smallint",
        "JSON": "nvarchar(max)",
        "ENUM": "nvarchar(max)",
        "SET": "nvarchar(max)",
        "BIT": "varbinary(8)",
        "UUID": "nvarchar(36)",
    }
    if logical.kind == "STRING":
        return (
            f"nvarchar({logical.length})"
            if logical.length and logical.length <= 4000
            else "nvarchar(max)"
        )
    target = mapping.get(logical.kind)
    if target is None:
        raise DomainError(
            "AZURE_SYNAPSE_TYPE_INCOMPATIBLE", "No safe Synapse type mapping is available", 422
        )
    return target


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    nullable: bool = True


def create_table_sql(
    schema: str,
    table: str,
    columns: list[Column],
    distribution: str = "ROUND_ROBIN",
    distribution_column: str | None = None,
) -> str:
    if not columns:
        raise DomainError("AZURE_SYNAPSE_SCHEMA_REQUIRED", "At least one column is required", 422)
    names = {column.name for column in columns}
    if len(names) != len(columns):
        raise DomainError("AZURE_SYNAPSE_SCHEMA_INVALID", "Column names must be unique", 422)
    if distribution == "HASH":
        if not distribution_column or distribution_column not in names:
            raise DomainError(
                "AZURE_SYNAPSE_DISTRIBUTION_INVALID",
                "HASH distribution requires an existing distribution column",
                422,
            )
        distribution_sql = f"HASH({quote_identifier(distribution_column)})"
    elif distribution in {"ROUND_ROBIN", "REPLICATE"}:
        distribution_sql = distribution
    else:
        raise DomainError(
            "AZURE_SYNAPSE_DISTRIBUTION_INVALID", "Unsupported distribution strategy", 422
        )
    fields = ", ".join(
        f"{quote_identifier(column.name)} {column.sql_type}"
        + (" NULL" if column.nullable else " NOT NULL")
        for column in columns
    )
    return (
        f"CREATE TABLE {quote_identifier(schema)}.{quote_identifier(table)} ({fields}) "
        f"WITH (DISTRIBUTION = {distribution_sql}, HEAP);"
    )


def copy_into_sql(schema: str, staging_table: str, storage_uri: str) -> str:
    if not re.fullmatch(
        r"https://[a-z0-9-]+\.(?:blob|dfs)\.core\.windows\.net/[A-Za-z0-9._~!$&()*+,;=:@%/-]+",
        storage_uri,
    ):
        raise DomainError("AZURE_STORAGE_URI_INVALID", "Staging URI is invalid", 422)
    return (
        f"COPY INTO {quote_identifier(schema)}.{quote_identifier(staging_table)} "
        f"FROM '{storage_uri}' WITH (FILE_TYPE = 'CSV', FIELDQUOTE = '\"', "
        "ROWTERMINATOR = '0x0a', ENCODING = 'UTF8', MAXERRORS = 0);"
    )


def append_apply_sql(schema: str, target: str, staging: str, columns: list[str]) -> str:
    selected = ", ".join(quote_identifier(column) for column in columns)
    if not selected:
        raise DomainError("AZURE_SYNAPSE_SCHEMA_REQUIRED", "At least one column is required", 422)
    return (
        f"INSERT INTO {quote_identifier(schema)}.{quote_identifier(target)} ({selected}) "
        f"SELECT {selected} FROM {quote_identifier(schema)}.{quote_identifier(staging)};"
    )


def stateful_apply_sql(*args, verified: bool = False, **kwargs) -> str:
    if not verified:
        raise DomainError(
            "AZURE_SYNAPSE_STATEFUL_NOT_VERIFIED",
            "Stateful CDC is disabled until Dedicated SQL Pool apply semantics pass cloud tests",
            422,
        )
    raise DomainError(
        "AZURE_SYNAPSE_STATEFUL_NOT_IMPLEMENTED",
        "No verified stateful Synapse apply strategy is installed",
        422,
    )
