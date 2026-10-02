"""remove obsolete object-storage and Iceberg delivery scope

Revision ID: 5e2d8a9f1c30
Revises: 3a91c8e7b4f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "5e2d8a9f1c30"
down_revision = "3a91c8e7b4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    connector_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT connector_id FROM pipeline_destinations "
                "WHERE delivery_type = 'ICEBERG' AND connector_id IS NOT NULL"
            )
        )
    ]
    storage_secret_ids = [
        row[0]
        for row in bind.execute(
            sa.text("SELECT secret_ref FROM connections WHERE category = 'OBJECT_STORAGE'")
        )
        if row[0] is not None
    ]

    op.execute("DELETE FROM pipeline_destinations WHERE delivery_type = 'ICEBERG'")
    for connector_id in connector_ids:
        bind.execute(sa.text("DELETE FROM connectors WHERE id = :id"), {"id": connector_id})

    with op.batch_alter_table("pipeline_events") as batch:
        batch.drop_index("ix_pipeline_events_lakehouse_destination_id")
        batch.drop_constraint("fk_pipeline_events_lakehouse_destination", type_="foreignkey")
        batch.drop_column("lakehouse_destination_id")

    with op.batch_alter_table("pipeline_destinations") as batch:
        batch.drop_index("ix_pipeline_destinations_lakehouse_destination_id")
        batch.drop_constraint("fk_pipeline_destinations_lakehouse_destination", type_="foreignkey")
        batch.drop_column("lakehouse_destination_id")
        batch.drop_column("delivery_type")
        batch.alter_column("destination_id", existing_type=sa.Uuid(), nullable=False)

    op.drop_table("lakehouse_destinations")
    op.execute("DELETE FROM connections WHERE category = 'OBJECT_STORAGE'")
    for secret_id in storage_secret_ids:
        bind.execute(
            sa.text(
                """
                DELETE FROM secret_references
                WHERE id = :id
                  AND NOT EXISTS (SELECT 1 FROM connections WHERE secret_ref = :id)
                  AND NOT EXISTS (SELECT 1 FROM sources WHERE secret_ref = :id)
                  AND NOT EXISTS (SELECT 1 FROM destinations WHERE secret_ref = :id)
                  AND NOT EXISTS (SELECT 1 FROM kafka_clusters WHERE secret_ref = :id)
                """
            ),
            {"id": secret_id},
        )
    op.drop_constraint("ck_connections_category", "connections", type_="check")
    op.create_check_constraint("ck_connections_category", "connections", "category = 'DATABASE'")


def downgrade() -> None:
    op.drop_constraint("ck_connections_category", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_category",
        "connections",
        "category IN ('DATABASE','OBJECT_STORAGE')",
    )
    op.create_table(
        "lakehouse_destinations",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("table_format", sa.String(length=20), nullable=False),
        sa.Column("storage_connection_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_connection_id", sa.Uuid(), nullable=True),
        sa.Column("query_engine_connection_id", sa.Uuid(), nullable=True),
        sa.Column("warehouse", sa.String(length=1000), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=20), nullable=False),
        sa.Column("write_mode", sa.String(length=20), nullable=False),
        sa.Column("delete_mode", sa.String(length=30), nullable=False),
        sa.Column("partition_config", sa.JSON(), nullable=False),
        sa.Column("identifier_fields", sa.JSON(), nullable=False),
        sa.Column("schema_evolution", sa.Boolean(), nullable=False),
        sa.Column("auto_create_tables", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["storage_connection_id"], ["connections.id"]),
        sa.ForeignKeyConstraint(["catalog_connection_id"], ["connections.id"]),
        sa.ForeignKeyConstraint(["query_engine_connection_id"], ["connections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table("pipeline_destinations") as batch:
        batch.alter_column("destination_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(
            sa.Column(
                "delivery_type",
                sa.String(length=30),
                nullable=False,
                server_default="DATABASE",
            )
        )
        batch.add_column(sa.Column("lakehouse_destination_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_pipeline_destinations_lakehouse_destination",
            "lakehouse_destinations",
            ["lakehouse_destination_id"],
            ["id"],
        )
        batch.create_index(
            "ix_pipeline_destinations_lakehouse_destination_id", ["lakehouse_destination_id"]
        )
    op.alter_column("pipeline_destinations", "delivery_type", server_default=None)
    with op.batch_alter_table("pipeline_events") as batch:
        batch.add_column(sa.Column("lakehouse_destination_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_pipeline_events_lakehouse_destination",
            "lakehouse_destinations",
            ["lakehouse_destination_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_pipeline_events_lakehouse_destination_id", ["lakehouse_destination_id"]
        )
