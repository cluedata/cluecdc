"""Allow four additional source providers without rewriting any existing records."""

from alembic import op
from sqlalchemy import text

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

OLD = "'POSTGRESQL','MYSQL','AWS_S3','MINIO','AZURE_SYNAPSE','CLICKHOUSE','BIGQUERY','REDSHIFT'"
NEW = "'MARIADB','SQLSERVER','MONGODB','ORACLE'"


def upgrade():
    with op.batch_alter_table("connections") as batch:
        batch.drop_constraint("ck_connections_provider", type_="check")
        batch.create_check_constraint("ck_connections_provider", f"provider IN ({OLD},{NEW})")


def downgrade():
    # Never delete new source records just to satisfy an older CHECK constraint.
    if op.get_bind().execute(text(f"SELECT 1 FROM connections WHERE provider IN ({NEW})")).first():
        raise RuntimeError("Remove new source connections explicitly before downgrading to 0013")
    with op.batch_alter_table("connections") as batch:
        batch.drop_constraint("ck_connections_provider", type_="check")
        batch.create_check_constraint("ck_connections_provider", f"provider IN ({OLD})")
