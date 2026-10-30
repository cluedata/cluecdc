"""Rename the Synapse-only ledger for shared staged warehouse runtimes."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("azure_synapse_batch_ledger", "warehouse_batch_ledger")


def downgrade():
    op.rename_table("warehouse_batch_ledger", "azure_synapse_batch_ledger")
