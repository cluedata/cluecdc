"""phase1_pipeline_lifecycle

Revision ID: 8f3b6a29c1d4
Revises: c25240d70048
"""

import sqlalchemy as sa
from alembic import op

revision = "8f3b6a29c1d4"
down_revision = "c25240d70048"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pipeline_tables",
        sa.Column(
            "primary_key_columns", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
    )
    op.add_column("pipeline_tables", sa.Column("destination_schema", sa.String(128)))
    op.add_column("pipeline_tables", sa.Column("destination_table", sa.String(128)))
    op.add_column(
        "pipeline_tables",
        sa.Column("initial_data_strategy", sa.String(), server_default="BACKFILL", nullable=False),
    )
    op.add_column(
        "pipeline_tables",
        sa.Column("delete_strategy", sa.String(), server_default="DELETE", nullable=False),
    )
    op.add_column(
        "pipeline_tables",
        sa.Column("schema_status", sa.String(), server_default="IN_SYNC", nullable=False),
    )
    op.add_column(
        "pipeline_tables",
        sa.Column("destination_status", sa.String(), server_default="READY", nullable=False),
    )
    op.add_column("pipeline_tables", sa.Column("snapshot_rows_processed", sa.BigInteger()))
    op.add_column("pipeline_tables", sa.Column("snapshot_estimated_rows", sa.BigInteger()))
    op.add_column("pipeline_tables", sa.Column("snapshot_current_chunk", sa.String(255)))
    op.add_column("pipeline_tables", sa.Column("snapshot_started_at", sa.DateTime(timezone=True)))
    op.add_column(
        "pipeline_tables", sa.Column("snapshot_last_activity_at", sa.DateTime(timezone=True))
    )
    op.add_column("pipeline_tables", sa.Column("snapshot_finished_at", sa.DateTime(timezone=True)))
    op.add_column("pipeline_tables", sa.Column("removed_at", sa.DateTime(timezone=True)))

    op.execute(
        """
        UPDATE pipeline_tables pt
        SET primary_key_columns = st.primary_key_columns
        FROM pipelines p
        JOIN source_tables st ON st.source_id = p.source_id
        WHERE pt.pipeline_id = p.id
          AND st.schema_name = pt.schema_name
          AND st.table_name = pt.table_name
        """
    )
    op.execute(
        """
        UPDATE pipeline_tables pt
        SET destination_schema = pt.schema_name,
            destination_table = pt.table_name,
            snapshot_status = CASE
                WHEN p.snapshot_mode = 'no_data' THEN 'NOT_REQUIRED'
                WHEN pt.snapshot_status = 'NOT_STARTED' THEN 'PENDING'
                ELSE pt.snapshot_status
            END
        FROM pipelines p
        WHERE p.id = pt.pipeline_id
        """
    )

    op.create_table(
        "pipeline_operations",
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("table_id", sa.Uuid()),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_step", sa.String(80)),
        sa.Column("progress", sa.Integer()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.String(1000)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipelines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_id"], ["pipeline_tables.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_operations_pipeline_id", "pipeline_operations", ["pipeline_id"])
    op.create_index("ix_pipeline_operations_table_id", "pipeline_operations", ["table_id"])
    op.create_index("ix_pipeline_operations_type", "pipeline_operations", ["type"])
    op.create_index("ix_pipeline_operations_status", "pipeline_operations", ["status"])
    op.create_index(
        "ix_pipeline_operations_pipeline_status",
        "pipeline_operations",
        ["pipeline_id", "status"],
    )


def downgrade():
    op.drop_index("ix_pipeline_operations_pipeline_status", table_name="pipeline_operations")
    op.drop_index("ix_pipeline_operations_status", table_name="pipeline_operations")
    op.drop_index("ix_pipeline_operations_type", table_name="pipeline_operations")
    op.drop_index("ix_pipeline_operations_table_id", table_name="pipeline_operations")
    op.drop_index("ix_pipeline_operations_pipeline_id", table_name="pipeline_operations")
    op.drop_table("pipeline_operations")
    for column in [
        "removed_at",
        "snapshot_finished_at",
        "snapshot_last_activity_at",
        "snapshot_started_at",
        "snapshot_current_chunk",
        "snapshot_estimated_rows",
        "snapshot_rows_processed",
        "destination_status",
        "schema_status",
        "delete_strategy",
        "initial_data_strategy",
        "destination_table",
        "destination_schema",
        "primary_key_columns",
    ]:
        op.drop_column("pipeline_tables", column)
