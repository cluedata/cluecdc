"""Add Azure Synapse connection support and durable staged-batch ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_connections_provider", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_provider",
        "connections",
        "provider IN ('POSTGRESQL','MYSQL','AWS_S3','MINIO','AZURE_SYNAPSE')",
    )
    op.create_table(
        "azure_synapse_batch_ledger",
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.String(length=120), nullable=False),
        sa.Column("source_offsets_json", sa.JSON(), nullable=False),
        sa.Column("staging_manifest_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PLANNED','STAGED','COPIED','APPLYING','APPLIED','FAILED')",
            name="ck_synapse_batch_status",
        ),
        sa.ForeignKeyConstraint(["delivery_id"], ["pipeline_destinations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", "batch_id"),
    )
    op.create_index(
        "ix_azure_synapse_batch_ledger_delivery_id",
        "azure_synapse_batch_ledger",
        ["delivery_id"],
    )
    op.create_index(
        "ix_azure_synapse_batch_ledger_status",
        "azure_synapse_batch_ledger",
        ["status"],
    )
    op.create_index(
        "ix_synapse_batch_recovery",
        "azure_synapse_batch_ledger",
        ["delivery_id", "status", "updated_at"],
    )


def downgrade():
    op.drop_table("azure_synapse_batch_ledger")
    op.drop_constraint("ck_connections_provider", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_provider",
        "connections",
        "provider IN ('POSTGRESQL','MYSQL','AWS_S3','MINIO')",
    )
