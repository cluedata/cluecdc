"""Expand the Synapse ledger for staging, apply, and checkpoint recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "azure_synapse_batch_ledger",
        sa.Column("checkpointed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_synapse_batch_status", "azure_synapse_batch_ledger", type_="check")
    op.execute(
        "UPDATE azure_synapse_batch_ledger SET status = CASE status "
        "WHEN 'PLANNED' THEN 'CREATED' WHEN 'COPIED' THEN 'STAGED' "
        "WHEN 'FAILED' THEN 'RETRYABLE_FAILED' ELSE status END"
    )
    op.create_check_constraint(
        "ck_synapse_batch_status",
        "azure_synapse_batch_ledger",
        "status IN ('CREATED','STAGING','STAGED','APPLYING','APPLIED','CHECKPOINTED',"
        "'RETRYABLE_FAILED','TERMINAL_FAILED')",
    )


def downgrade():
    op.drop_constraint("ck_synapse_batch_status", "azure_synapse_batch_ledger", type_="check")
    op.execute(
        "UPDATE azure_synapse_batch_ledger SET status = CASE status "
        "WHEN 'CREATED' THEN 'PLANNED' WHEN 'STAGING' THEN 'PLANNED' "
        "WHEN 'CHECKPOINTED' THEN 'APPLIED' WHEN 'RETRYABLE_FAILED' THEN 'FAILED' "
        "WHEN 'TERMINAL_FAILED' THEN 'FAILED' ELSE status END"
    )
    op.create_check_constraint(
        "ck_synapse_batch_status",
        "azure_synapse_batch_ledger",
        "status IN ('PLANNED','STAGED','COPIED','APPLYING','APPLIED','FAILED')",
    )
    op.drop_column("azure_synapse_batch_ledger", "checkpointed_at")
