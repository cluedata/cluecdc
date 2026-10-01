import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LogicalType:
    kind: str
    precision: int | None = None
    scale: int | None = None
    length: int | None = None
    unsigned: bool = False
    source_type: str = ""


@dataclass(frozen=True)
class TypeMapping:
    source_type: str
    logical_type: str
    destination_type: str | None
    compatibility: str
    message: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _numbers(value: str) -> tuple[int | None, int | None]:
    match = re.search(r"\((\d+)(?:\s*,\s*(\d+))?\)", value)
    return (
        int(match.group(1)) if match else None,
        int(match.group(2)) if match and match.group(2) else None,
    )


def parse_type(provider: str, value: str) -> LogicalType:
    raw = value.strip()
    text = raw.lower()
    first, second = _numbers(text)
    if provider == "mysql":
        unsigned = " unsigned" in text
        base = re.split(r"[\s(]", text, maxsplit=1)[0]
        if base in {"bool", "boolean"} or (base == "tinyint" and first == 1):
            return LogicalType("BOOLEAN", source_type=raw)
        widths = {
            "tinyint": 8,
            "smallint": 16,
            "mediumint": 24,
            "int": 32,
            "integer": 32,
            "bigint": 64,
        }
        if base in widths:
            return LogicalType(f"INT{widths[base]}", unsigned=unsigned, source_type=raw)
        if base in {"decimal", "numeric"}:
            return LogicalType("DECIMAL", first or 10, second or 0, source_type=raw)
        if base in {"float", "real"}:
            return LogicalType("FLOAT32", source_type=raw)
        if base == "double":
            return LogicalType("FLOAT64", source_type=raw)
        if base in {"char", "varchar"}:
            return LogicalType("STRING", length=first, source_type=raw)
        if base in {"tinytext", "text", "mediumtext", "longtext", "enum", "set"}:
            return LogicalType(base.upper(), source_type=raw)
        if base in {"binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob"}:
            return LogicalType("BYTES", length=first, source_type=raw)
        if base in {"date", "time", "datetime", "timestamp", "year"}:
            return LogicalType(base.upper(), precision=first, source_type=raw)
        if base == "json":
            return LogicalType("JSON", source_type=raw)
        if base == "bit":
            return LogicalType("BIT", length=first or 1, source_type=raw)
        if base in {"geometry", "point", "linestring", "polygon"}:
            return LogicalType("SPATIAL", source_type=raw)
    else:
        if text in {"smallint", "int2"}:
            return LogicalType("INT16", source_type=raw)
        if text in {"integer", "int", "int4"}:
            return LogicalType("INT32", source_type=raw)
        if text in {"bigint", "int8"}:
            return LogicalType("INT64", source_type=raw)
        if text.startswith(("numeric", "decimal")):
            return LogicalType("DECIMAL", first, second, source_type=raw)
        if text == "real":
            return LogicalType("FLOAT32", source_type=raw)
        if text == "double precision":
            return LogicalType("FLOAT64", source_type=raw)
        if text == "boolean":
            return LogicalType("BOOLEAN", source_type=raw)
        if text.startswith(("character varying", "varchar", "character(", "char(")):
            return LogicalType("STRING", length=first, source_type=raw)
        if text in {"text", "uuid"}:
            return LogicalType("UUID" if text == "uuid" else "TEXT", source_type=raw)
        if text == "bytea":
            return LogicalType("BYTES", source_type=raw)
        if text == "date":
            return LogicalType("DATE", source_type=raw)
        if text.startswith("time") and not text.startswith("timestamp"):
            return LogicalType("TIME", precision=first, source_type=raw)
        if text.startswith("timestamp"):
            return LogicalType(
                "TIMESTAMPTZ" if "with time zone" in text else "DATETIME",
                precision=first,
                source_type=raw,
            )
        if text in {"json", "jsonb"}:
            return LogicalType("JSON", source_type=raw)
    return LogicalType("UNSUPPORTED", source_type=raw)


def destination_type(logical: LogicalType, provider: str) -> TypeMapping:
    logical_name = (
        f"U{logical.kind}" if logical.unsigned and logical.kind.startswith("INT") else logical.kind
    )
    warning = logical.kind in {"ENUM", "SET", "YEAR", "BIT", "UUID"}
    incompatible = logical.kind in {"UNSUPPORTED", "SPATIAL"}
    if incompatible:
        return TypeMapping(
            logical.source_type,
            logical_name,
            None,
            "INCOMPATIBLE",
            "No safe portable mapping is available",
        )
    if provider == "postgresql":
        mapping = {
            "BOOLEAN": "boolean",
            "INT8": "smallint",
            "INT16": "smallint",
            "INT24": "integer",
            "INT32": "bigint" if logical.unsigned else "integer",
            "INT64": "numeric(20,0)" if logical.unsigned else "bigint",
            "FLOAT32": "real",
            "FLOAT64": "double precision",
            "STRING": None,
            "TEXT": "text",
            "TINYTEXT": "text",
            "MEDIUMTEXT": "text",
            "LONGTEXT": "text",
            "BYTES": "bytea",
            "DATE": "date",
            "TIME": "time",
            "DATETIME": "timestamp without time zone",
            "TIMESTAMP": "timestamp without time zone",
            "TIMESTAMPTZ": "timestamp with time zone",
            "YEAR": "smallint",
            "JSON": "jsonb",
            "ENUM": "text",
            "SET": "text",
            "BIT": "bit varying",
            "UUID": "uuid",
        }
        target = mapping.get(logical.kind)
        if logical.kind == "STRING":
            target = f"character varying({logical.length})" if logical.length else "text"
        if logical.kind == "DECIMAL":
            target = (
                f"numeric({logical.precision},{logical.scale})"
                if logical.precision is not None
                else "numeric"
            )
    else:
        mapping = {
            "BOOLEAN": "boolean",
            "INT8": "tinyint",
            "INT16": "smallint",
            "INT24": "mediumint",
            "INT32": "int",
            "INT64": "bigint",
            "FLOAT32": "float",
            "FLOAT64": "double",
            "TEXT": "longtext",
            "TINYTEXT": "tinytext",
            "MEDIUMTEXT": "mediumtext",
            "LONGTEXT": "longtext",
            "BYTES": "longblob",
            "DATE": "date",
            "TIME": "time(6)",
            "DATETIME": "datetime(6)",
            "TIMESTAMP": "datetime(6)",
            "TIMESTAMPTZ": "timestamp(6)",
            "YEAR": "year",
            "JSON": "json",
            "ENUM": "longtext",
            "SET": "longtext",
            "BIT": f"bit({logical.length or 1})",
            "UUID": "char(36)",
        }
        target = mapping.get(logical.kind)
        if logical.kind == "STRING":
            target = f"varchar({min(logical.length, 16383)})" if logical.length else "longtext"
            warning = bool(logical.length and logical.length > 16383)
        if logical.kind == "DECIMAL":
            if logical.precision and logical.precision > 65:
                return TypeMapping(
                    logical.source_type,
                    logical_name,
                    None,
                    "INCOMPATIBLE",
                    "MySQL DECIMAL precision is limited to 65",
                )
            target = f"decimal({logical.precision or 65},{logical.scale or 0})"
    if target is None:
        return TypeMapping(
            logical.source_type, logical_name, None, "INCOMPATIBLE", "No destination type mapping"
        )
    return TypeMapping(
        logical.source_type,
        logical_name,
        target,
        "WARNING" if warning else "COMPATIBLE",
        "Mapped to a portable representation; engine-specific constraints are not preserved"
        if warning
        else None,
    )


def analyze_columns(
    columns: list[dict], source_provider: str, destination_provider: str
) -> list[dict]:
    results = []
    for column in columns:
        logical = parse_type(source_provider, column["type"])
        mapped = destination_type(logical, destination_provider).as_dict()
        results.append({**column, **mapped})
    return results
