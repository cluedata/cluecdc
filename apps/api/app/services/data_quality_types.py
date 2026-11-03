"""Conservative canonical types; compatibility means no demonstrated loss."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def canonical(column: dict) -> dict:
    name = str(column["data_type"]).lower()
    native = str(column.get("native_type") or name).lower()
    result = {"family": "unknown", "native": native}
    integers = {
        "smallint": 16,
        "tinyint": 8,
        "mediumint": 24,
        "integer": 32,
        "int": 32,
        "bigint": 64,
    }
    if name in integers:
        bits = integers[name]
        unsigned = "unsigned" in native
        return {"family": "integer", "bits": bits, "unsigned": unsigned}
    if name in {"numeric", "decimal"}:
        return {
            "family": "decimal",
            "precision": column.get("numeric_precision"),
            "scale": column.get("numeric_scale"),
        }
    if name in {"real", "double precision", "float", "double"}:
        return {"family": "float", "bits": 32 if name in {"real", "float"} else 64}
    if name in {
        "character varying",
        "varchar",
        "text",
        "character",
        "char",
        "tinytext",
        "mediumtext",
        "longtext",
    }:
        # Fixed character padding has different comparison/storage semantics.
        return {
            "family": "fixed_string" if name in {"character", "char"} else "string",
            "length": column.get("character_maximum_length"),
            "charset": column.get("character_set_name"),
        }
    if name in {"bytea", "blob", "tinyblob", "mediumblob", "longblob", "varbinary", "binary"}:
        return {
            "family": "binary",
            "length": column.get("character_maximum_length"),
            "fixed": name == "binary",
        }
    if name in {"timestamp with time zone", "timestamp without time zone", "timestamp", "datetime"}:
        return {
            "family": "timestamp",
            "aware": name == "timestamp with time zone",
            "precision": column.get("datetime_precision", 6),
            "native": name,
        }
    if name in {"date", "boolean", "json", "jsonb"}:
        return {"family": name}
    return result


def compatible(source: dict, destination: dict, exact: bool = False) -> bool | None:
    if "unknown" in (source["family"], destination["family"]):
        return None
    if exact:
        return source == destination
    if source["family"] != destination["family"]:
        return False
    family = source["family"]
    if family == "integer":

        def limits(t):
            bits = t["bits"]
            return (0, 2**bits - 1) if t["unsigned"] else (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1)

        low, high = limits(source)
        dest_low, dest_high = limits(destination)
        return dest_low <= low and dest_high >= high
    if family == "decimal":
        p, s = source["precision"], source["scale"]
        dp, ds = destination["precision"], destination["scale"]
        if dp is None:
            return True
        if None in (p, s, ds):
            return False
        return ds >= s and dp - ds >= p - s
    if family in {"string", "fixed_string", "binary"}:
        if source.get("charset") != destination.get("charset"):
            return False
        if source.get("fixed") != destination.get("fixed"):
            return False
        length, dest_length = source["length"], destination["length"]
        return dest_length is None or (length is not None and dest_length >= length)
    if family == "float":
        return destination["bits"] >= source["bits"]
    if family == "timestamp":
        return (
            source["aware"] == destination["aware"]
            and source["native"] == destination["native"]
            and destination["precision"] >= source["precision"]
        )
    return True


def normalize_timestamp(value: datetime | None, zone: str | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    if not zone:
        raise ValueError("NAIVE_TIMEZONE_REQUIRED")
    tz = ZoneInfo(zone)
    first = value.replace(tzinfo=tz, fold=0)
    second = value.replace(tzinfo=tz, fold=1)
    # Ambiguous/nonexistent wall times cannot be interpreted safely.
    if first.utcoffset() != second.utcoffset():
        raise ValueError("AMBIGUOUS_LOCAL_TIME")
    if first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != value:
        raise ValueError("NONEXISTENT_LOCAL_TIME")
    return first.astimezone(UTC)


def cross_provider_compatible(source: dict, destination: dict, exact: bool = False) -> bool | None:
    """Verified PostgreSQL -> MySQL schema domains; no generic coercion claims.

    Count/MAX comparisons do not require lossless conversion of whole schemas.
    Schema Match must distinguish a proven narrowing from an unverified mapping.
    In particular, timezone-free MySQL metadata does not preserve PostgreSQL's
    timezone-qualified schema contract merely because observations normalize to UTC.
    """
    source_family, destination_family = source["family"], destination["family"]
    if "unknown" in (source_family, destination_family):
        return None
    if source_family != destination_family:
        return False
    if source_family == "integer":
        return compatible(source, destination, exact)
    if source_family == "timestamp" and source["aware"] != destination["aware"]:
        return False
    if source_family == "decimal":
        # A size/range narrowing is demonstrably unsafe. A widening alone does
        # not certify PostgreSQL numeric special values for MySQL DECIMAL.
        return False if compatible(source, destination, exact) is False else None
    if source_family == "float" and destination["bits"] < source["bits"]:
        return False
    if source_family in {"string", "fixed_string", "binary"}:
        length, target_length = source["length"], destination["length"]
        if length is not None and target_length is not None and target_length < length:
            return False
    # Charset/collation, date ranges, JSON and special-value domains require
    # independent verification. Returning None prevents false schema PASS.
    return None
