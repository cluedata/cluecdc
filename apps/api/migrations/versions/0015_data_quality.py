"""Add observational Data Quality metadata; existing CDC tables are untouched."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def entity():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "data_quality_checks",
        *entity(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column(
            "source_connection_id", sa.Uuid(), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column(
            "destination_connection_id", sa.Uuid(), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column("pipeline_id", sa.Uuid(), sa.ForeignKey("pipelines.id")),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("active_run_id", sa.Uuid()),
        sa.Column("alert_state_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_data_quality_checks_next_run_at", "data_quality_checks", ["next_run_at"])
    op.create_table(
        "data_quality_rules",
        *entity(),
        sa.Column(
            "check_id",
            sa.Uuid(),
            sa.ForeignKey("data_quality_checks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_type", sa.String(30), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
    )
    op.create_index("ix_data_quality_rules_check_id", "data_quality_rules", ["check_id"])
    op.create_table(
        "data_quality_runs",
        *entity(),
        sa.Column("check_id", sa.Uuid(), sa.ForeignKey("data_quality_checks.id"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("occurrence", sa.String(120), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("quality", sa.String(20)),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("window_json", sa.JSON(), nullable=False),
        sa.Column("consistency", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.UniqueConstraint("check_id", "occurrence", name="uq_dq_occurrence"),
    )
    op.create_index("ix_data_quality_runs_check_id", "data_quality_runs", ["check_id"])
    op.create_index("ix_data_quality_runs_status", "data_quality_runs", ["status"])
    op.create_table(
        "data_quality_rule_results",
        *entity(),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("data_quality_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("rule_type", sa.String(30), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "rule_id", name="uq_dq_rule_result"),
    )
    op.create_index("ix_data_quality_rule_results_run_id", "data_quality_rule_results", ["run_id"])


def downgrade():
    for table in (
        "data_quality_rule_results",
        "data_quality_runs",
        "data_quality_rules",
        "data_quality_checks",
    ):
        op.drop_table(table)
