from prometheus_client import Counter, Gauge, Histogram

ALERTS_ACTIVE = Gauge("cluecdc_alerts_active", "Active alerts", ["severity", "event_type"])
ALERTS_TOTAL = Counter("cluecdc_alerts_total", "Alerts created", ["severity", "event_type"])
NOTIFICATIONS_SENT = Counter("cluecdc_notifications_sent_total", "Notifications sent", ["provider"])
NOTIFICATIONS_FAILED = Counter(
    "cluecdc_notifications_failed_total", "Notification attempts failed", ["provider"]
)
DELIVERY_DURATION = Histogram(
    "cluecdc_notification_delivery_duration_seconds",
    "Notification provider request duration",
    ["provider", "status"],
)
