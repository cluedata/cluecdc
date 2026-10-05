"""Data-preserving bridge from the last prototype schema, not a blind stamp."""

import sqlalchemy as sa
from alembic import op

REFERENCES = (
    ("pipelines", "source_id", "source_connection_id", "sources", None),
    ("source_tables", "source_id", "source_connection_id", "sources", "CASCADE"),
    ("schema_versions", "source_id", "source_connection_id", "sources", "CASCADE"),
    ("pipeline_destinations", "destination_id", "destination_connection_id", "destinations", None),
    ("pipeline_events", "destination_id", "destination_connection_id", "destinations", "SET NULL"),
)


def upgrade_legacy(bind):
    inspector = sa.inspect(bind)
    if not {"sources", "destinations", "connections"}.issubset(inspector.get_table_names()):
        raise RuntimeError("Legacy metadata schema is incomplete; restore from backup")
    metadata = sa.MetaData()
    connection = sa.Table("connections", metadata, autoload_with=bind)
    mirrors = {
        name: sa.Table(name, metadata, autoload_with=bind) for name in ("sources", "destinations")
    }
    known = {row["id"]: dict(row) for row in bind.execute(sa.select(connection)).mappings()}
    rows_by_table = {
        name: list(bind.execute(sa.select(table)).mappings()) for name, table in mirrors.items()
    }
    # Refuse unknown providers/conflicting identities before changing anything.
    for row in known.values():
        if row["category"] != "DATABASE" or row["provider"] not in {"POSTGRESQL", "MYSQL"}:
            raise RuntimeError("Unsupported legacy connection; review metadata before upgrading")
    for rows in rows_by_table.values():
        for row in rows:
            existing = known.get(row["id"])
            if row["type"] not in {"postgresql", "mysql"} or (
                existing and existing["provider"] != row["type"].upper()
            ):
                raise RuntimeError(
                    "Conflicting legacy endpoint identity; review metadata before upgrading"
                )

    names = {row["name"] for row in known.values()}
    for table_name, capability in (("sources", "SOURCE"), ("destinations", "DESTINATION")):
        for row in rows_by_table[table_name]:
            existing = known.get(row["id"])
            if existing:
                if existing["secret_ref"] != row["secret_ref"]:
                    raise RuntimeError(
                        "Conflicting legacy credential references; review metadata before upgrading"
                    )
                capabilities = sorted(set(existing["capabilities_json"] or []) | {capability})
                bind.execute(
                    connection.update()
                    .where(connection.c.id == row["id"])
                    .values(capabilities_json=capabilities)
                )
                existing["capabilities_json"] = capabilities
                continue
            name = row["name"]
            if name in names:
                suffix = " (" + capability.lower() + " " + str(row["id"]) + ")"
                name = name[: 120 - len(suffix)] + suffix
            names.add(name)
            values = {
                "id": row["id"],
                "name": name,
                "category": "DATABASE",
                "provider": row["type"].upper(),
                "status": row["status"],
                "description": row.get("description", ""),
                "config_json": {
                    key: row[key]
                    for key in (
                        "environment",
                        "host",
                        "port",
                        "database_name",
                        "username",
                        "ssl_enabled",
                        "provider_options",
                    )
                },
                "capabilities_json": [capability],
                "secret_ref": row["secret_ref"],
                "last_tested_at": row["last_health_check_at"],
                "last_test_status": None,
                "last_test_message": None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            bind.execute(connection.insert().values(**values))
            known[row["id"]] = values

    for table, old_column, new_column, old_target, ondelete in REFERENCES:
        matching = [
            fk
            for fk in inspector.get_foreign_keys(table)
            if fk["constrained_columns"] == [old_column] and fk["referred_table"] == old_target
        ]
        if len(matching) != 1:
            raise RuntimeError("Unexpected legacy foreign key; restore from backup")
        op.drop_constraint(matching[0]["name"], table, type_="foreignkey")
        op.alter_column(table, old_column, new_column_name=new_column)
        op.create_foreign_key(
            f"{table}_{new_column}_fkey",
            table,
            "connections",
            [new_column],
            ["id"],
            ondelete=ondelete,
        )
        # Column renaming preserves existing unique constraints and their rows.
        for index in inspector.get_indexes(table):
            if index["name"] == f"ix_{table}_{old_column}":
                op.drop_index(index["name"], table_name=table)
                op.create_index(f"ix_{table}_{new_column}", table, [new_column])

    op.add_column(
        "pipeline_destinations",
        sa.Column("delivery_type", sa.String(30), nullable=False, server_default="DATABASE"),
    )
    op.alter_column("pipeline_destinations", "delivery_type", server_default=None)
    op.add_column("jobs", sa.Column("claimed_by", sa.String(120), nullable=True))
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "jobs",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.alter_column("jobs", "attempt_count", server_default=None)
    op.alter_column("jobs", "next_attempt_at", server_default=None)
    # Old RUNNING claims had no owners/leases and must be made claimable again.
    bind.execute(sa.text("UPDATE jobs SET status='PENDING' WHERE status='RUNNING'"))
    op.create_index("ix_jobs_claim", "jobs", ["status", "next_attempt_at", "lease_expires_at"])
    op.create_index("ix_jobs_claimed_by", "jobs", ["claimed_by"])
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"])
    op.add_column("notification_deliveries", sa.Column("claimed_by", sa.String(160), nullable=True))
    op.add_column(
        "notification_deliveries",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind.execute(
        sa.text("UPDATE notification_deliveries SET status='pending' WHERE status='sending'")
    )
    op.drop_constraint("ck_connections_category", "connections", type_="check")
    op.create_check_constraint(
        "ck_connections_category", "connections", "category IN ('DATABASE','OBJECT_STORAGE')"
    )
    op.create_check_constraint(
        "ck_connections_provider",
        "connections",
        "provider IN ('POSTGRESQL','MYSQL','AWS_S3','MINIO')",
    )
    # All references now use connections; mirror data has been promoted without
    # changing IDs, secrets, connector names, topics or offset ownership.
    op.drop_table("sources")
    op.drop_table("destinations")
