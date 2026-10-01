"""database provider options

Revision ID: b71c9e4d2a10
Revises: 8f3b6a29c1d4
"""

import sqlalchemy as sa
from alembic import op

revision = "b71c9e4d2a10"
down_revision = "8f3b6a29c1d4"
branch_labels = None
depends_on = None


def upgrade():
    empty = sa.text("'{}'::json")
    op.add_column(
        "sources", sa.Column("provider_options", sa.JSON(), server_default=empty, nullable=False)
    )
    op.add_column(
        "destinations",
        sa.Column("provider_options", sa.JSON(), server_default=empty, nullable=False),
    )
    op.execute("UPDATE sources SET type = 'postgresql' WHERE type IS NULL OR type = ''")
    op.execute("UPDATE destinations SET type = 'postgresql' WHERE type IS NULL OR type = ''")


def downgrade():
    op.drop_column("destinations", "provider_options")
    op.drop_column("sources", "provider_options")
