"""Normalize authentication uniqueness to named unique indexes."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


AUTH_UNIQUE_INDEXES = (
    ("users", "email", "ix_users_email"),
    ("invites", "token_hash", "ix_invites_token_hash"),
    ("auth_sessions", "token_hash", "ix_auth_sessions_token_hash"),
)


def upgrade():
    connection = op.get_bind()

    for table_name, column_name, index_name in AUTH_UNIQUE_INDEXES:
        inspector = sa.inspect(connection)
        for constraint in inspector.get_unique_constraints(table_name):
            if constraint.get("column_names") == [column_name] and constraint.get("name"):
                op.drop_constraint(constraint["name"], table_name, type_="unique")

        inspector = sa.inspect(connection)
        indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
        current = indexes.get(index_name)
        if current and not current.get("unique"):
            op.drop_index(index_name, table_name=table_name)
            current = None
        if current is None:
            op.create_index(index_name, table_name, [column_name], unique=True)


def downgrade():
    # Revision 0002 already defines the same unique indexes. This migration
    # only repairs databases created from an earlier draft of that revision.
    pass
