"""Add ClickHouse, BigQuery, and Redshift connection providers."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_connections_provider", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_provider",
        "connections",
        "provider IN ('POSTGRESQL','MYSQL','AWS_S3','MINIO','AZURE_SYNAPSE',"
        "'CLICKHOUSE','BIGQUERY','REDSHIFT')",
    )


def downgrade():
    op.drop_constraint("ck_connections_provider", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_provider",
        "connections",
        "provider IN ('POSTGRESQL','MYSQL','AWS_S3','MINIO','AZURE_SYNAPSE')",
    )
