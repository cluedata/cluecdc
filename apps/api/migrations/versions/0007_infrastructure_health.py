"""Persist Kafka and Kafka Connect health observations."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("kafka_clusters", "connect_clusters"):
        op.add_column(table, sa.Column("status_reason", sa.String(length=500)))
        op.add_column(table, sa.Column("last_health_check_at", sa.DateTime(timezone=True)))


def downgrade():
    for table in ("connect_clusters", "kafka_clusters"):
        op.drop_column(table, "last_health_check_at")
        op.drop_column(table, "status_reason")
