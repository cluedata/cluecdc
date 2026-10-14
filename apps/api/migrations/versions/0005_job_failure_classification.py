"""Add observable bounded job retry metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("error_code", sa.String(length=80), nullable=True))
    op.add_column("jobs", sa.Column("error_category", sa.String(length=80), nullable=True))
    op.add_column("jobs", sa.Column("retryable", sa.Boolean(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "failure_history_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.alter_column("jobs", "failure_history_json", server_default=None)
    op.add_column("jobs", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("jobs", "last_attempt_at")
    op.drop_column("jobs", "failure_history_json")
    op.drop_column("jobs", "retryable")
    op.drop_column("jobs", "error_category")
    op.drop_column("jobs", "error_code")
