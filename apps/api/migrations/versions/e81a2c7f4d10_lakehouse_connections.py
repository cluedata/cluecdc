"""lakehouse connections and destinations

Revision ID: e81a2c7f4d10
Revises: d94b8a71e620
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "e81a2c7f4d10"
down_revision = "d94b8a71e620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("secret_ref", sa.Uuid(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(length=20), nullable=True),
        sa.Column("last_test_message", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('DATABASE','OBJECT_STORAGE','CATALOG','QUERY_ENGINE','STREAMING')",
            name="ck_connections_category",
        ),
        sa.ForeignKeyConstraint(["secret_ref"], ["secret_references.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_connections_category", "connections", ["category"])
    op.create_index("ix_connections_provider", "connections", ["provider"])
    op.create_index("ix_connections_status", "connections", ["status"])

    op.create_table(
        "lakehouse_destinations",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("table_format", sa.String(length=20), nullable=False),
        sa.Column("storage_connection_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_connection_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(["catalog_connection_id"], ["connections.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["query_engine_connection_id"], ["connections.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["storage_connection_id"], ["connections.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        "ix_lakehouse_destinations_storage_connection_id",
        "lakehouse_destinations",
        ["storage_connection_id"],
    )
    op.create_index(
        "ix_lakehouse_destinations_catalog_connection_id",
        "lakehouse_destinations",
        ["catalog_connection_id"],
    )
    op.create_index(
        "ix_lakehouse_destinations_query_engine_connection_id",
        "lakehouse_destinations",
        ["query_engine_connection_id"],
    )
    op.create_index("ix_lakehouse_destinations_status", "lakehouse_destinations", ["status"])

    with op.batch_alter_table("pipeline_destinations") as batch:
        batch.alter_column("destination_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(sa.Column("lakehouse_destination_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column(
                "delivery_type", sa.String(length=30), nullable=False, server_default="DATABASE"
            )
        )
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
        batch.add_column(sa.Column("delivery_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("lakehouse_destination_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("component", sa.String(length=80), nullable=False, server_default="pipeline")
        )
        batch.add_column(sa.Column("error_code", sa.String(length=80), nullable=True))
        batch.add_column(
            sa.Column("technical_details", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("recoverable", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.create_foreign_key(
            "fk_pipeline_events_delivery",
            "pipeline_destinations",
            ["delivery_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_pipeline_events_lakehouse_destination",
            "lakehouse_destinations",
            ["lakehouse_destination_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_pipeline_events_delivery_id", ["delivery_id"])
        batch.create_index(
            "ix_pipeline_events_lakehouse_destination_id", ["lakehouse_destination_id"]
        )
    op.alter_column("pipeline_events", "component", server_default=None)
    op.alter_column("pipeline_events", "technical_details", server_default=None)
    op.alter_column("pipeline_events", "recoverable", server_default=None)


def downgrade() -> None:
    # Downgrade is safe only when no Lakehouse deliveries remain. PostgreSQL will reject
    # restoring NOT NULL if such rows exist, preserving data instead of deleting it.
    with op.batch_alter_table("pipeline_events") as batch:
        batch.drop_index("ix_pipeline_events_lakehouse_destination_id")
        batch.drop_index("ix_pipeline_events_delivery_id")
        batch.drop_constraint("fk_pipeline_events_lakehouse_destination", type_="foreignkey")
        batch.drop_constraint("fk_pipeline_events_delivery", type_="foreignkey")
        batch.drop_column("recoverable")
        batch.drop_column("technical_details")
        batch.drop_column("error_code")
        batch.drop_column("component")
        batch.drop_column("lakehouse_destination_id")
        batch.drop_column("delivery_id")
    with op.batch_alter_table("pipeline_destinations") as batch:
        batch.drop_index("ix_pipeline_destinations_lakehouse_destination_id")
        batch.drop_constraint("fk_pipeline_destinations_lakehouse_destination", type_="foreignkey")
        batch.drop_column("delivery_type")
        batch.drop_column("lakehouse_destination_id")
        batch.alter_column("destination_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("lakehouse_destinations")
    op.drop_table("connections")
