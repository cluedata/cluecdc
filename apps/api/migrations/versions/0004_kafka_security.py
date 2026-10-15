"""Add non-secret Kafka client security configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "kafka_clusters",
        sa.Column(
            "security_config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.alter_column("kafka_clusters", "security_config_json", server_default=None)


def downgrade():
    op.drop_column("kafka_clusters", "security_config_json")
