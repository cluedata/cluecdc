"""unify database and lakehouse integrations as connections

Revision ID: f19b7a4c2e60
Revises: e81a2c7f4d10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "f19b7a4c2e60"
down_revision = "e81a2c7f4d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("capabilities_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    connection_table = sa.table(
        "connections",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("category", sa.String()),
        sa.column("provider", sa.String()),
        sa.column("status", sa.String()),
        sa.column("description", sa.String()),
        sa.column("config_json", sa.JSON()),
        sa.column("capabilities_json", sa.JSON()),
        sa.column("secret_ref", sa.Uuid()),
        sa.column("last_tested_at", sa.DateTime(timezone=True)),
        sa.column("last_test_status", sa.String()),
        sa.column("last_test_message", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    capability_by_category = {
        "OBJECT_STORAGE": ["DESTINATION"],
        "CATALOG": ["DESTINATION"],
        "QUERY_ENGINE": ["QUERY"],
        "STREAMING": [],
    }
    existing = bind.execute(sa.select(connection_table)).mappings().all()
    known_ids = {row["id"] for row in existing}
    known_names = {str(row["name"]) for row in existing}
    for row in existing:
        bind.execute(
            connection_table.update()
            .where(connection_table.c.id == row["id"])
            .values(capabilities_json=capability_by_category.get(row["category"], []))
        )

    def promote(table: str, capability: str) -> None:
        columns = (
            "id, name, type, environment, host, port, database_name, username, "
            "secret_ref, ssl_enabled, provider_options, status, last_health_check_at, "
            "created_at, updated_at"
        )
        if table == "destinations":
            columns += ", description"
        rows = bind.execute(sa.text(f"SELECT {columns} FROM {table}")).mappings()
        for row in rows:
            if row["id"] in known_ids:
                continue
            name = str(row["name"])
            candidate = name
            counter = 1
            while candidate in known_names:
                suffix = f" ({capability.lower()}{'' if counter == 1 else f' {counter}'})"
                candidate = name + suffix
                counter += 1
            known_names.add(candidate)
            known_ids.add(row["id"])
            bind.execute(
                connection_table.insert().values(
                    id=row["id"],
                    name=candidate,
                    category="DATABASE",
                    provider=str(row["type"]).upper(),
                    status=row["status"],
                    description=row.get("description", ""),
                    config_json={
                        "environment": row["environment"],
                        "host": row["host"],
                        "port": row["port"],
                        "database_name": row["database_name"],
                        "username": row["username"],
                        "ssl_enabled": row["ssl_enabled"],
                        "provider_options": row["provider_options"] or {},
                    },
                    capabilities_json=[capability],
                    secret_ref=row["secret_ref"],
                    last_tested_at=row["last_health_check_at"],
                    last_test_status=row["status"],
                    last_test_message=None,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )

    promote("sources", "SOURCE")
    promote("destinations", "DESTINATION")
    op.alter_column("connections", "capabilities_json", server_default=None)


def downgrade() -> None:
    op.drop_column("connections", "capabilities_json")
