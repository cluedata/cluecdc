"""align the legacy lakehouse catalog foreign key with the nullable model

Revision ID: 3a91c8e7b4f2
Revises: a24c9d71e308
"""

from collections.abc import Sequence

from alembic import op

revision = "3a91c8e7b4f2"
down_revision = "a24c9d71e308"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "lakehouse_destinations_catalog_connection_id_fkey",
        "lakehouse_destinations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "lakehouse_destinations_catalog_connection_id_fkey",
        "lakehouse_destinations",
        "connections",
        ["catalog_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "lakehouse_destinations_catalog_connection_id_fkey",
        "lakehouse_destinations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "lakehouse_destinations_catalog_connection_id_fkey",
        "lakehouse_destinations",
        "connections",
        ["catalog_connection_id"],
        ["id"],
        ondelete="RESTRICT",
    )
