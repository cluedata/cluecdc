# Alerting and notifications

ClueCDC alerting is best-effort observability around the CDC control plane. A notification
failure is recorded for troubleshooting, but never fails a pipeline lifecycle operation.

## Architecture

Kafka Connect, source, destination, and pipeline monitors produce normalized `AlertEvent`
objects through the `AlertEventBus` interface. The current bus is in-process; the interface can
be replaced by Kafka, Redis, or NATS without changing event producers.

`AlertManager` fingerprints events, correlates recovery with the active alert, evaluates rules,
and queues one `notification_deliveries` row per matching channel. The worker delivers queued
notifications separately from reconciliation. Slack, Telegram, and generic webhook providers
implement the same provider contract. A failure in one provider does not stop other deliveries.

Delivery retries are bounded. Authentication/authorization and other permanent HTTP failures
are not retried. Transient failures are retried after 5 seconds, 30 seconds, and 2 minutes (one
initial delivery plus three retries).
Provider failures never produce another alert, preventing recursive alert loops.

## Alert lifecycle

Statuses are `firing`, `acknowledged`, `silenced`, and `resolved`.

- Repeated observations with the same event, source, pipeline, connector, and task fingerprint
  update `last_seen_at` and `occurrence_count`; they do not create duplicate alerts.
- Acknowledgement records the actor and time but does not claim the incident recovered.
- Silence suppresses notifications while monitoring and alert history continue.
- A healthy observation resolves the existing active alert and records recovery duration. If the
  matching rule enables recovery messages, its channels receive a resolved notification.
- A recurrence after resolution creates a new alert. Its rule's cooldown policy decides whether
  a new notification is queued.

Policies are `notify_after_cooldown` (default), `notify_every_occurrence`, and
`notify_first_occurrence_only`.

## Supported events

Kafka Connect events:

- `CONNECTOR_FAILED`, `CONNECTOR_UNASSIGNED`, `CONNECTOR_PAUSED`
- `CONNECTOR_RESTART_LOOP`, `CONNECT_TASK_FAILED`, `KAFKA_CONNECT_UNAVAILABLE`

Source CDC events:

- `SOURCE_CONNECTION_FAILED`, `SOURCE_DATABASE_UNAVAILABLE`, `SOURCE_AUTH_FAILED`
- `CDC_REPLICATION_SLOT_ERROR`, `CDC_BINLOG_ERROR`, `CDC_WAL_ERROR`
- `CDC_PERMISSION_ERROR`, `CDC_CONNECTOR_FAILED`

Destination events:

- `DESTINATION_CONNECTION_FAILED`, `DESTINATION_AUTH_FAILED`
- `DESTINATION_WRITE_FAILED`, `DESTINATION_SCHEMA_ERROR`, `DESTINATION_CONNECTOR_FAILED`

Pipeline events:

- `PIPELINE_FAILED`, `PIPELINE_DEGRADED`, `PIPELINE_NO_EVENTS`, `PIPELINE_LAG_HIGH`

`PIPELINE_NO_EVENTS` and `PIPELINE_LAG_HIGH` require an installed stream metrics provider; ClueCDC
does not infer or fabricate lag when that provider is unavailable.

The classifier recognizes common PostgreSQL replication slot/WAL/permission errors, MySQL
binlog/privilege errors, authentication errors, and destination schema failures. The sanitized
underlying error remains in alert details.

Severities are `critical`, `warning`, and `info`. Resolved is a lifecycle state rather than a
severity.

## Create notification channels

Open **Alerts → Notification channels**. Credentials are encrypted with the existing Fernet
secret provider. API responses contain only `configured` and a masked value. Leaving a secret
field blank while editing retains the stored secret.

### Slack Incoming Webhook

1. In Slack, create or select an app for the target workspace.
2. Enable **Incoming Webhooks**.
3. Choose **Add New Webhook to Workspace**, select the target channel, and authorize it.
4. Copy the `https://hooks.slack.com/services/...` URL.
5. Add a Slack channel in ClueCDC and select **Test**.

The test message is `ClueCDC test notification`. Only HTTPS URLs on `hooks.slack.com` are
accepted.

### Telegram

1. Message `@BotFather` in Telegram and run `/newbot`.
2. Copy the Bot Token returned by BotFather.
3. Add the bot to the destination group.
4. Send a message in that group, then call
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`. Group IDs are
   commonly negative values.
5. Enter the token and Chat ID in ClueCDC and select **Test**.

If `getUpdates` is empty, remove any webhook configured for that bot, send another group message,
and try again. Treat the bot token as a password.

### Generic webhook

Configure an HTTPS endpoint, optional `Authorization` value, and optional JSON custom headers.
ClueCDC sends an object containing `event`, `severity`, `status`, `timestamp`, `pipeline`,
`connector`, `error`, and `alert`.

Loopback, link-local, private, `.local`, `.internal`, and cloud metadata destinations are blocked.
DNS is checked again immediately before delivery. `Host`, `Content-Length`, and transfer encoding
headers cannot be overridden.

## Create an alert rule

Open **Alerts → Alert rules**, select severity and event types, select one or more notification
channels, then choose cooldown and recovery behavior. An empty event selection matches all event
types. The installation migration creates an enabled `Critical CDC Errors` routing rule with no
channels, so it cannot send until an administrator attaches channels.

The default rule covers connector/task/Kafka Connect availability, source/destination connection,
and pipeline failures. Alerting remains inert when no channel is assigned.

## REST API

- `GET /api/v1/alerts`, `GET /api/v1/alerts/{id}`
- `GET /api/v1/alerts/summary`
- `POST /api/v1/alerts/{id}/acknowledge`, `POST /api/v1/alerts/{id}/silence`, and
  `POST /api/v1/alerts/{id}/unsilence`
- CRUD `/api/v1/alert-rules`
- CRUD `/api/v1/notification-channels`
- `POST /api/v1/notification-channels/{id}/test`

Alert listing accepts `page`, `pageSize`, `status`, `severity`, `eventType`, `pipelineId`, and
`sourceId`.

## Security and operations

- Never put tokens in channel names, rule descriptions, or alert messages.
- Secrets are neither returned by the API nor included in audit/structured logs.
- Rotate a secret by editing a channel and entering the replacement value.
- Metrics are exposed at `/metrics`: active/total alerts, sent/failed notifications, and delivery
  duration. Labels are bounded to severity, event type, provider, and status.
- Audit records cover channel/rule create, update, and delete plus acknowledge and silence actions.

## Troubleshooting

- A delivery in `pending` is waiting for its next retry. `failed` includes a sanitized provider
  error and attempt count on the alert detail page.
- HTTP 401/403 generally means a revoked webhook or invalid token; update the channel secret and
  test it.
- Telegram 400 commonly indicates an incorrect Chat ID or a bot that is not a group member.
- If no delivery exists, verify the rule is enabled, the severity/event filters match, and at least
  one enabled channel is selected.
- If alerts do not appear, verify the background worker is enabled and Kafka Connect reconciliation
  is running. Database connection alerts are emitted by source/destination health checks.
