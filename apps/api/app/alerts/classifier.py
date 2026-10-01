class ErrorClassifier:
    """Maps raw infrastructure errors into stable ClueCDC event types."""

    RULES = (
        (("replication slot", "does not exist"), "CDC_REPLICATION_SLOT_ERROR"),
        (("requested wal segment",), "CDC_WAL_ERROR"),
        (("wal", "removed"), "CDC_WAL_ERROR"),
        (("binary logging is not enabled",), "CDC_BINLOG_ERROR"),
        (("binlog", "purged"), "CDC_BINLOG_ERROR"),
        (("server-id", "conflict"), "CDC_BINLOG_ERROR"),
        (("permission denied", "replication"), "CDC_PERMISSION_ERROR"),
        (("replication privilege",), "CDC_PERMISSION_ERROR"),
        (("connection refused",), "SOURCE_DATABASE_UNAVAILABLE"),
        (("database is unavailable",), "SOURCE_DATABASE_UNAVAILABLE"),
        (("connection timed out",), "SOURCE_DATABASE_UNAVAILABLE"),
        (("authentication failed",), "SOURCE_AUTH_FAILED"),
        (("access denied",), "SOURCE_AUTH_FAILED"),
        (("constraint violation",), "DESTINATION_WRITE_FAILED"),
        (("duplicate key",), "DESTINATION_WRITE_FAILED"),
        (("schema", "incompatible"), "DESTINATION_SCHEMA_ERROR"),
        (("does not exist",), "DESTINATION_SCHEMA_ERROR"),
        (("connectexception",), "CONNECT_TASK_FAILED"),
    )

    def classify(self, raw_error: object, fallback: str) -> str:
        text = str(raw_error).lower()
        for needles, event_type in self.RULES:
            if all(needle in text for needle in needles):
                return event_type
        return fallback
