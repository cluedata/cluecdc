"""Repair legacy keyless pipelines classified as current-state sync."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE pipeline_tables
        SET sync_mode = 'CHANGE_EVENT_LOG'
        WHERE sync_mode = 'CURRENT_STATE'
          AND (
            effective_key_mode = 'NONE'
            OR effective_key_columns IS NULL
            OR CAST(effective_key_columns AS TEXT) IN ('[]', 'null')
          )
        """
    )


def downgrade():
    # This is a data repair. Reverting it would reintroduce an unsafe mode and
    # cannot distinguish repaired legacy rows from intentional event-log rows.
    pass
