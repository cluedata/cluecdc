"""Recognize the last prototype revision without replaying obsolete schemas.

Fresh databases pass through this empty checkpoint. Existing databases at this
revision are converted by 0001, preserving resource and credential identities.
"""

revision = "5e2d8a9f1c30"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
