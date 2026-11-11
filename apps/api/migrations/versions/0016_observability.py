"""Add cached observational state and nullable sample identity; retain legacy history."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pipeline_tables", sa.Column("snapshot_observation_json", sa.JSON()))
    op.add_column("pipeline_metrics", sa.Column("sample_bucket", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_pipeline_metrics_history", "pipeline_metrics", ["pipeline_id", "created_at"]
    )
    op.create_unique_constraint(
        "uq_observability_bucket", "pipeline_metrics", ["pipeline_id", "sample_bucket"]
    )
    op.create_table(
        "observability_collection_state",
        sa.Column(
            "pipeline_id",
            sa.Uuid(),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("next_due", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("latest_json", sa.JSON(), nullable=False),
        sa.Column("analysis_json", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_observability_collection_state_next_due", "observability_collection_state", ["next_due"]
    )


def downgrade():
    op.drop_column("pipeline_tables", "snapshot_observation_json")
    op.drop_table("observability_collection_state")
    op.drop_constraint("uq_observability_bucket", "pipeline_metrics", type_="unique")
    op.drop_index("ix_pipeline_metrics_history", table_name="pipeline_metrics")
    op.drop_column("pipeline_metrics", "sample_bucket")
