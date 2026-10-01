"""alerting and notification infrastructure

Revision ID: d94b8a71e620
Revises: b71c9e4d2a10
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "d94b8a71e620"
down_revision = "b71c9e4d2a10"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("alert_rules", "name", type_=sa.String(length=120))
    op.add_column(
        "alert_rules", sa.Column("description", sa.String(1000), nullable=False, server_default="")
    )
    op.add_column("alert_rules", sa.Column("severity", sa.String(20), nullable=True))
    op.add_column(
        "alert_rules", sa.Column("event_types", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "alert_rules", sa.Column("source_filters", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "alert_rules", sa.Column("pipeline_filters", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "alert_rules",
        sa.Column("connector_filters", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "alert_rules",
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="900"),
    )
    op.add_column(
        "alert_rules",
        sa.Column(
            "notification_policy",
            sa.String(40),
            nullable=False,
            server_default="notify_after_cooldown",
        ),
    )
    op.add_column(
        "alert_rules",
        sa.Column("send_recovery", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "notification_channels",
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("config_encrypted", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["config_encrypted"], ["secret_references.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_notification_channels_type", "notification_channels", ["type"])
    op.create_index("ix_notification_channels_enabled", "notification_channels", ["enabled"])

    op.create_table(
        "alert_rule_channels",
        sa.Column("alert_rule_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["alert_rule_id"], ["alert_rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("alert_rule_id", "channel_id"),
    )
    op.create_table(
        "alerts",
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=True),
        sa.Column("source_name", sa.String(255), nullable=True),
        sa.Column("pipeline_id", sa.Uuid(), nullable=True),
        sa.Column("pipeline_name", sa.String(120), nullable=True),
        sa.Column("component", sa.String(80), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.String(2000), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(255), nullable=True),
        sa.Column("silenced_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("silenced_by", sa.String(255), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipelines.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in [
        ("ix_alerts_fingerprint", ["fingerprint"]),
        ("ix_alerts_event_type", ["event_type"]),
        ("ix_alerts_severity", ["severity"]),
        ("ix_alerts_status", ["status"]),
        ("ix_alerts_source_id", ["source_id"]),
        ("ix_alerts_pipeline_id", ["pipeline_id"]),
        ("ix_alerts_fingerprint_status", ["fingerprint", "status"]),
        ("ix_alerts_pipeline_severity", ["pipeline_id", "severity"]),
        ("ix_alerts_created_at", ["created_at"]),
    ]:
        op.create_index(name, "alerts", columns)
    op.create_index(
        "uq_alerts_active_fingerprint",
        "alerts",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status IN ('firing', 'acknowledged', 'silenced')"),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(1000), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_id", "channel_id", "kind"),
    )
    op.create_index("ix_notification_deliveries_alert_id", "notification_deliveries", ["alert_id"])
    op.create_index(
        "ix_notification_deliveries_channel_id", "notification_deliveries", ["channel_id"]
    )
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])
    op.create_index(
        "ix_notification_delivery_queue", "notification_deliveries", ["status", "next_attempt_at"]
    )

    # Useful immediately, but deliberately has no channels until an admin configures one.
    op.execute(
        sa.text(
            """INSERT INTO alert_rules
            (id, name, description, enabled, severity, event_types, source_filters,
             pipeline_filters, connector_filters, cooldown_seconds, notification_policy,
             send_recovery, config_json, created_at, updated_at)
            SELECT :id, 'Critical CDC Errors', 'Critical infrastructure and CDC failures',
             true, 'critical', CAST(:events AS JSON), CAST('[]' AS JSON),
             CAST('[]' AS JSON), CAST('[]' AS JSON), 900,
             'notify_after_cooldown', true, CAST('{}' AS JSON),
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (
                SELECT 1 FROM alert_rules WHERE name = 'Critical CDC Errors'
            )"""
        ).bindparams(
            id=uuid.uuid4(),
            events='["CONNECTOR_FAILED","CONNECT_TASK_FAILED","KAFKA_CONNECT_UNAVAILABLE",'
            '"SOURCE_CONNECTION_FAILED","DESTINATION_CONNECTION_FAILED","PIPELINE_FAILED"]',
        )
    )


def downgrade():
    op.drop_table("notification_deliveries")
    op.drop_table("alerts")
    op.drop_table("alert_rule_channels")
    op.drop_table("notification_channels")
    for column in [
        "send_recovery",
        "notification_policy",
        "cooldown_seconds",
        "connector_filters",
        "pipeline_filters",
        "source_filters",
        "event_types",
        "severity",
        "description",
    ]:
        op.drop_column("alert_rules", column)
    op.alter_column("alert_rules", "name", type_=sa.String())
