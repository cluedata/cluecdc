"""Serialize connector lifecycle operations with expiring ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("connectors", sa.Column("operation_token", sa.String(length=32)))
    op.add_column("connectors", sa.Column("operation_name", sa.String(length=40)))
    op.add_column("connectors", sa.Column("operation_started_at", sa.DateTime(timezone=True)))
    op.create_index("ix_connectors_operation_token", "connectors", ["operation_token"])


def downgrade():
    op.drop_index("ix_connectors_operation_token", table_name="connectors")
    op.drop_column("connectors", "operation_started_at")
    op.drop_column("connectors", "operation_name")
    op.drop_column("connectors", "operation_token")
