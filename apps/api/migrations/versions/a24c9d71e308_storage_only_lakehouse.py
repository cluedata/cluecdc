"""reduce Lakehouse integrations to S3 and MinIO storage

Revision ID: a24c9d71e308
Revises: f19b7a4c2e60
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "a24c9d71e308"
down_revision = "f19b7a4c2e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    removed_secret_refs = [
        row[0]
        for row in bind.execute(
            sa.text(
                """
                SELECT secret_ref
                FROM connections
                WHERE category NOT IN ('DATABASE', 'OBJECT_STORAGE')
                   OR provider NOT IN (
                       'POSTGRESQL', 'MYSQL', 'SQL_SERVER', 'ORACLE',
                       'AWS_S3', 'MINIO'
                   )
                """
            )
        )
        if row[0] is not None
    ]

    op.alter_column(
        "lakehouse_destinations",
        "catalog_connection_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.execute(
        sa.text(
            """
            UPDATE lakehouse_destinations
            SET catalog_connection_id = NULL,
                query_engine_connection_id = NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM connections
            WHERE category NOT IN ('DATABASE', 'OBJECT_STORAGE')
               OR provider NOT IN (
                   'POSTGRESQL', 'MYSQL', 'SQL_SERVER', 'ORACLE',
                   'AWS_S3', 'MINIO'
               )
            """
        )
    )

    for secret_ref in removed_secret_refs:
        bind.execute(
            sa.text(
                """
                DELETE FROM secret_references
                WHERE id = :secret_ref
                  AND NOT EXISTS (
                      SELECT 1 FROM connections WHERE connections.secret_ref = :secret_ref
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM sources WHERE sources.secret_ref = :secret_ref
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM destinations WHERE destinations.secret_ref = :secret_ref
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM kafka_clusters WHERE kafka_clusters.secret_ref = :secret_ref
                  )
                """
            ),
            {"secret_ref": secret_ref},
        )

    op.drop_constraint("ck_connections_category", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_category",
        "connections",
        "category IN ('DATABASE','OBJECT_STORAGE')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_connections_category", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_category",
        "connections",
        "category IN ('DATABASE','OBJECT_STORAGE','CATALOG','QUERY_ENGINE','STREAMING')",
    )
