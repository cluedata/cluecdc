"""Rename the Synapse-only ledger for shared staged warehouse runtimes."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("azure_synapse_batch_ledger", "warehouse_batch_ledger")
    op.execute(
        "ALTER INDEX ix_azure_synapse_batch_ledger_delivery_id "
        "RENAME TO ix_warehouse_batch_ledger_delivery_id"
    )
    op.execute(
        "ALTER INDEX ix_azure_synapse_batch_ledger_status "
        "RENAME TO ix_warehouse_batch_ledger_status"
    )


def downgrade():
    op.execute(
        "ALTER INDEX ix_warehouse_batch_ledger_delivery_id "
        "RENAME TO ix_azure_synapse_batch_ledger_delivery_id"
    )
    op.execute(
        "ALTER INDEX ix_warehouse_batch_ledger_status "
        "RENAME TO ix_azure_synapse_batch_ledger_status"
    )
    op.rename_table("warehouse_batch_ledger", "azure_synapse_batch_ledger")
