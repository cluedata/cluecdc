from app.core.errors import DomainError


def validate_fixed_history_schema(
    actual: dict[str, str], expected: dict[str, str], *, provider: str, table: str
) -> None:
    mismatched = {
        name: {"expected": column_type, "actual": actual.get(name)}
        for name, column_type in expected.items()
        if actual.get(name) != column_type
    }
    if mismatched:
        raise DomainError(
            f"{provider}_TABLE_SCHEMA_MISMATCH",
            f"{provider.title()} target table does not match the append-only history schema",
            422,
            {"table": table, "columns": mismatched},
        )
