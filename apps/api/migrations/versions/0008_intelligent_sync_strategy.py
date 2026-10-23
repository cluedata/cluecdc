"""Add effective row-key and synchronization strategy metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "source_tables",
        sa.Column(
            "source_properties_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("source_tables", "source_properties_json", server_default=None)

    op.add_column("pipeline_tables", sa.Column("sync_mode", sa.String(length=30)))
    op.add_column("pipeline_tables", sa.Column("effective_key_mode", sa.String(length=30)))
    op.add_column("pipeline_tables", sa.Column("effective_key_columns", sa.JSON()))
    op.add_column("pipeline_tables", sa.Column("effective_key_name", sa.String(length=255)))
    op.add_column("pipeline_tables", sa.Column("effective_key_enforcement", sa.String(length=30)))
    op.add_column(
        "pipeline_tables",
        sa.Column(
            "sync_capabilities_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.execute(
        """
        UPDATE pipeline_tables
        SET sync_mode = 'CURRENT_STATE',
            effective_key_mode = CASE
                WHEN primary_key_columns IS NOT NULL
                 AND CAST(primary_key_columns AS TEXT) NOT IN ('[]', 'null')
                THEN 'PRIMARY_KEY'
                ELSE 'NONE'
            END,
            effective_key_columns = primary_key_columns,
            effective_key_enforcement = CASE
                WHEN primary_key_columns IS NOT NULL
                 AND CAST(primary_key_columns AS TEXT) NOT IN ('[]', 'null')
                THEN 'DATABASE_ENFORCED'
                ELSE NULL
            END
        """
    )
    op.alter_column("pipeline_tables", "sync_capabilities_json", server_default=None)


def downgrade():
    op.drop_column("pipeline_tables", "sync_capabilities_json")
    op.drop_column("pipeline_tables", "effective_key_enforcement")
    op.drop_column("pipeline_tables", "effective_key_name")
    op.drop_column("pipeline_tables", "effective_key_columns")
    op.drop_column("pipeline_tables", "effective_key_mode")
    op.drop_column("pipeline_tables", "sync_mode")
    op.drop_column("source_tables", "source_properties_json")
