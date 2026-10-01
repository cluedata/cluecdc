"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, Send, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button, Dialog, Input } from "@cluecdc/ui";
import type {
  AlertDetail,
  AlertPage,
  AlertRule,
  NotificationChannel,
  Pipeline,
  Source,
  Connector,
} from "@cluecdc/contracts";
import { api, date, post, relativeTime } from "@/lib/api";
import {
  DataTable,
  Empty,
  ErrorPanel,
  Field,
  Loading,
  PageHeader,
  Tabs,
} from "./common";
import { Status } from "./common";

const EVENT_TYPES = [
  "CONNECTOR_FAILED",
  "CONNECTOR_UNASSIGNED",
  "CONNECTOR_PAUSED",
  "CONNECTOR_RESTART_LOOP",
  "CONNECT_TASK_FAILED",
  "KAFKA_CONNECT_UNAVAILABLE",
  "SOURCE_CONNECTION_FAILED",
  "SOURCE_DATABASE_UNAVAILABLE",
  "SOURCE_AUTH_FAILED",
  "CDC_REPLICATION_SLOT_ERROR",
  "CDC_BINLOG_ERROR",
  "CDC_WAL_ERROR",
  "CDC_PERMISSION_ERROR",
  "DESTINATION_CONNECTION_FAILED",
  "DESTINATION_AUTH_FAILED",
  "DESTINATION_WRITE_FAILED",
  "DESTINATION_SCHEMA_ERROR",
  "DESTINATION_CONNECTOR_FAILED",
  "PIPELINE_FAILED",
  "PIPELINE_DEGRADED",
  "PIPELINE_NO_EVENTS",
  "PIPELINE_LAG_HIGH",
];

function invalidateAlerts(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: ["alerts"] });
  client.invalidateQueries({ queryKey: ["alert-summary"] });
}

export function AlertsPage() {
  const [tab, setTab] = useState("Active");
  const [severity, setSeverity] = useState("");
  const [eventType, setEventType] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [fromDate, setFromDate] = useState("");
  const status =
    tab === "Active" ? "active" : tab === "Resolved" ? "resolved" : "";
  const query = useQuery({
    queryKey: ["alerts", status, severity, eventType, pipeline],
    queryFn: () => {
      const params = new URLSearchParams({ pageSize: "100" });
      if (status) params.set("status", status);
      if (severity) params.set("severity", severity);
      if (eventType) params.set("eventType", eventType);
      if (pipeline) params.set("pipelineId", pipeline);
      return api<AlertPage>(`/alerts?${params}`);
    },
    refetchInterval: 10000,
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
  });
  const items = query.data?.items.filter(
    (item) =>
      !fromDate ||
      new Date(item.first_seen_at) >= new Date(`${fromDate}T00:00:00`),
  );
  return (
    <>
      <PageHeader
        title="Alerts"
        description="Correlated CDC infrastructure incidents, recovery state, and notification delivery."
        eyebrow="OPERATIONS / ALERTS"
      >
        <Button asChild variant="outline">
          <Link href="/alerts/rules">Alert rules</Link>
        </Button>
        <Button asChild>
          <Link href="/alerts/channels">Notification channels</Link>
        </Button>
      </PageHeader>
      <Tabs
        tabs={["Active", "Resolved", "All"]}
        active={tab}
        onChange={setTab}
      />
      <div className="filter-bar alert-filters">
        <select
          aria-label="Alert severity"
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
        >
          <option value="">All severities</option>
          <option value="critical">Critical</option>
          <option value="warning">Warning</option>
          <option value="info">Info</option>
        </select>
        <select
          aria-label="Alert event type"
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
        >
          <option value="">All event types</option>
          {EVENT_TYPES.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <select
          aria-label="Alert pipeline"
          value={pipeline}
          onChange={(e) => setPipeline(e.target.value)}
        >
          <option value="">All pipelines</option>
          {pipelines.data?.map((value) => (
            <option value={value.id} key={value.id}>
              {value.name}
            </option>
          ))}
        </select>
        <Input
          aria-label="Alerts from date"
          type="date"
          value={fromDate}
          onChange={(e) => setFromDate(e.target.value)}
        />
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : items?.length ? (
        <DataTable
          data={items}
          getRowHref={(row) => `/alerts/${row.id}`}
          columns={[
            {
              accessorKey: "severity",
              header: "Severity",
              cell: ({ row }) => <Status value={row.original.severity} />,
            },
            {
              accessorKey: "event_type",
              header: "Alert",
              cell: ({ row }) => (
                <div className="alert-title-cell">
                  <strong>{row.original.title}</strong>
                  <small>{row.original.event_type}</small>
                </div>
              ),
            },
            {
              accessorKey: "source_name",
              header: "Source",
              cell: ({ row }) => row.original.source_name || "Unavailable",
            },
            {
              accessorKey: "pipeline_name",
              header: "Pipeline",
              cell: ({ row }) => row.original.pipeline_name || "Unavailable",
            },
            {
              accessorKey: "status",
              header: "Status",
              cell: ({ row }) => <Status value={row.original.status} />,
            },
            { accessorKey: "occurrence_count", header: "Occurrences" },
            {
              accessorKey: "first_seen_at",
              header: "Started",
              cell: ({ row }) => relativeTime(row.original.first_seen_at),
            },
          ]}
        />
      ) : (
        <Empty
          title="No alerts"
          description="Everything is running normally."
        />
      )}
    </>
  );
}

export function AlertDetailPage({ id }: { id: string }) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["alert", id],
    queryFn: () => api<AlertDetail>(`/alerts/${id}`),
    refetchInterval: 10000,
  });
  const action = useMutation({
    mutationFn: ({
      kind,
      duration,
    }: {
      kind: "acknowledge" | "silence" | "unsilence";
      duration?: number | null;
    }) =>
      post(
        `/alerts/${id}/${kind}`,
        kind === "silence" ? { duration_seconds: duration } : undefined,
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["alert", id] });
      invalidateAlerts(client);
      toast.success("Alert updated");
    },
    onError: (error) => toast.error(error.message),
  });
  if (query.isPending) return <Loading />;
  if (query.isError)
    return <ErrorPanel error={query.error} retry={() => query.refetch()} />;
  const alert = query.data;
  const taskId = alert.details.task_id;
  return (
    <>
      <PageHeader
        title={alert.event_type}
        description={alert.title}
        eyebrow="ALERT DETAIL"
      >
        {alert.status === "firing" && (
          <Button
            variant="outline"
            disabled={action.isPending}
            onClick={() => action.mutate({ kind: "acknowledge" })}
          >
            Acknowledge
          </Button>
        )}
        {alert.status === "silenced" && (
          <Button
            variant="outline"
            disabled={action.isPending}
            onClick={() => action.mutate({ kind: "unsilence" })}
          >
            Unsilence
          </Button>
        )}
        {alert.status !== "resolved" && (
          <select
            aria-label="Silence alert"
            defaultValue=""
            onChange={(e) =>
              e.target.value &&
              action.mutate({
                kind: "silence",
                duration:
                  e.target.value === "manual" ? null : Number(e.target.value),
              })
            }
          >
            <option value="">Silence…</option>
            <option value="1800">30 minutes</option>
            <option value="3600">1 hour</option>
            <option value="14400">4 hours</option>
            <option value="86400">24 hours</option>
            <option value="manual">Until manually enabled</option>
          </select>
        )}
      </PageHeader>
      <div className="alert-detail-layout">
        <section className="panel">
          <div className="alert-detail-heading">
            <Status value={alert.severity} />
            <Status value={alert.status} />
          </div>
          <dl className="facts alert-facts">
            <dt>Pipeline</dt>
            <dd>{alert.pipeline_name || "Unavailable"}</dd>
            <dt>Source</dt>
            <dd>{alert.source_name || "Unavailable"}</dd>
            <dt>Component</dt>
            <dd>{alert.component}</dd>
            <dt>Connector</dt>
            <dd>{String(alert.details.connector || "Unavailable")}</dd>
            <dt>Task</dt>
            <dd>
              {taskId === null || taskId === undefined
                ? "Unavailable"
                : String(taskId)}
            </dd>
            <dt>First seen</dt>
            <dd>{date(alert.first_seen_at)}</dd>
            <dt>Last seen</dt>
            <dd>{date(alert.last_seen_at)}</dd>
            <dt>Occurrences</dt>
            <dd>{alert.occurrence_count}</dd>
            {alert.resolved_at && (
              <>
                <dt>Resolved</dt>
                <dd>{date(alert.resolved_at)}</dd>
              </>
            )}
          </dl>
          <div className="alert-error-heading">
            <h2>Error</h2>
            <Button
              variant="ghost"
              onClick={() => {
                navigator.clipboard.writeText(alert.message);
                toast.success("Error copied");
              }}
            >
              Copy
            </Button>
          </div>
          <pre className="alert-error">{alert.message}</pre>
          <details>
            <summary>Technical details</summary>
            <pre className="json-view">
              {JSON.stringify(alert.details, null, 2)}
            </pre>
          </details>
        </section>
        <section className="panel">
          <h2>Notification deliveries</h2>
          {alert.deliveries.length ? (
            <div className="delivery-list">
              {alert.deliveries.map((delivery) => (
                <div key={delivery.id}>
                  <span className="channel-icon">
                    {delivery.channel_type.slice(0, 1).toUpperCase()}
                  </span>
                  <div>
                    <strong>{delivery.channel_name}</strong>
                    <small>
                      {delivery.channel_type} · {delivery.kind} ·{" "}
                      {delivery.sent_at
                        ? date(delivery.sent_at)
                        : `Attempt ${delivery.attempt_count}`}
                    </small>
                    {delivery.last_error && (
                      <small className="field-error">
                        {delivery.last_error}
                      </small>
                    )}
                  </div>
                  <Status value={delivery.status} />
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No notification rule matched this alert.</p>
          )}
        </section>
      </div>
    </>
  );
}

type ChannelForm = {
  id?: string;
  name: string;
  type: "slack" | "telegram" | "webhook";
  webhookUrl: string;
  botToken: string;
  chatId: string;
  url: string;
  authorization: string;
  headers: string;
};

const emptyChannel: ChannelForm = {
  name: "",
  type: "slack",
  webhookUrl: "",
  botToken: "",
  chatId: "",
  url: "",
  authorization: "",
  headers: "",
};

export function NotificationChannelsPage() {
  const client = useQueryClient();
  const [editor, setEditor] = useState<ChannelForm | null>(null);
  const query = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => api<NotificationChannel[]>("/notification-channels"),
  });
  const save = useMutation({
    mutationFn: async (form: ChannelForm) => {
      let headers: Record<string, string> = {};
      if (form.headers.trim()) headers = JSON.parse(form.headers);
      const config =
        form.type === "slack"
          ? { webhook_url: form.webhookUrl }
          : form.type === "telegram"
            ? { bot_token: form.botToken, chat_id: form.chatId }
            : { url: form.url, authorization: form.authorization, headers };
      return api(
        form.id
          ? `/notification-channels/${form.id}`
          : "/notification-channels",
        {
          method: form.id ? "PUT" : "POST",
          body: JSON.stringify(
            form.id
              ? { name: form.name, config }
              : { name: form.name, type: form.type, enabled: true, config },
          ),
        },
      );
    },
    onSuccess: () => {
      setEditor(null);
      client.invalidateQueries({ queryKey: ["notification-channels"] });
      toast.success("Notification channel saved");
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Unable to save channel",
      ),
  });
  const operate = useMutation({
    mutationFn: ({
      channel,
      action,
    }: {
      channel: NotificationChannel;
      action: "test" | "toggle" | "delete";
    }) =>
      action === "test"
        ? post(`/notification-channels/${channel.id}/test`)
        : api(
            `/notification-channels/${channel.id}`,
            action === "delete"
              ? { method: "DELETE" }
              : {
                  method: "PUT",
                  body: JSON.stringify({
                    enabled: !channel.enabled,
                    config: {},
                  }),
                },
          ),
    onSuccess: (_, value) => {
      client.invalidateQueries({ queryKey: ["notification-channels"] });
      toast.success(
        value.action === "test"
          ? "Test notification sent successfully"
          : "Notification channel updated",
      );
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <>
      <PageHeader
        title="Notification channels"
        description="Secure delivery endpoints used by one or more alert rules."
        eyebrow="ALERTS / CHANNELS"
      >
        <Button asChild variant="outline">
          <Link href="/alerts">Alert history</Link>
        </Button>
        <Button onClick={() => setEditor({ ...emptyChannel })}>
          <Plus size={15} /> Add channel
        </Button>
      </PageHeader>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} />
      ) : query.data.length ? (
        <div className="channel-grid">
          {query.data.map((channel) => (
            <section className="panel channel-card" key={channel.id}>
              <div className="channel-card-heading">
                <span className={`channel-logo channel-${channel.type}`}>
                  {channel.type.slice(0, 1).toUpperCase()}
                </span>
                <div>
                  <strong>{channel.name}</strong>
                  <small>{channel.type}</small>
                </div>
                <Status value={channel.enabled ? "CONNECTED" : "DISABLED"} />
              </div>
              <code>{channel.config.maskedValue}</code>
              <div className="row-actions">
                <Button
                  variant="outline"
                  disabled={operate.isPending || !channel.enabled}
                  onClick={() => operate.mutate({ channel, action: "test" })}
                >
                  <Send size={14} /> Test
                </Button>
                <Button
                  variant="ghost"
                  onClick={() =>
                    setEditor({
                      ...emptyChannel,
                      id: channel.id,
                      name: channel.name,
                      type: channel.type,
                    })
                  }
                >
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => operate.mutate({ channel, action: "toggle" })}
                >
                  {channel.enabled ? "Disable" : "Enable"}
                </Button>
                <Button
                  variant="ghost"
                  aria-label={`Delete ${channel.name}`}
                  onClick={() =>
                    confirm(`Delete ${channel.name}?`) &&
                    operate.mutate({ channel, action: "delete" })
                  }
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            </section>
          ))}
        </div>
      ) : (
        <Empty
          title="No notification channels yet"
          description="Get notified when your CDC pipelines have problems."
          onAction={() => setEditor({ ...emptyChannel })}
          action="Add notification channel"
        />
      )}
      <ChannelDialog
        key={editor?.id || (editor ? "new" : "closed")}
        form={editor}
        onClose={() => setEditor(null)}
        onSave={(value) => save.mutate(value)}
        saving={save.isPending}
      />
    </>
  );
}

function ChannelDialog({
  form,
  onClose,
  onSave,
  saving,
}: {
  form: ChannelForm | null;
  onClose: () => void;
  onSave: (form: ChannelForm) => void;
  saving: boolean;
}) {
  const [draft, setDraft] = useState(form);
  const update = (key: keyof ChannelForm, value: string) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  return (
    <Dialog
      open={!!form}
      onOpenChange={(open) => !open && onClose()}
      title={
        form?.id ? "Edit notification channel" : "Add notification channel"
      }
      description="Credentials are encrypted at rest and never returned after saving."
    >
      {draft && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSave(draft);
          }}
        >
          {!draft.id && (
            <Field label="Provider">
              <select
                value={draft.type}
                onChange={(e) => update("type", e.target.value)}
              >
                <option value="slack">Slack</option>
                <option value="telegram">Telegram</option>
                <option value="webhook">Generic webhook</option>
              </select>
            </Field>
          )}
          <Field label="Channel name">
            <Input
              required
              value={draft.name}
              onChange={(e) => update("name", e.target.value)}
            />
          </Field>
          {draft.type === "slack" && (
            <Field
              label="Webhook URL"
              hint={
                draft.id
                  ? "Leave blank to retain the existing secret."
                  : "Create an Incoming Webhook in your Slack app."
              }
            >
              <Input
                required={!draft.id}
                type="password"
                autoComplete="new-password"
                placeholder={
                  draft.id ? "••••••••" : "https://hooks.slack.com/services/..."
                }
                value={draft.webhookUrl}
                onChange={(e) => update("webhookUrl", e.target.value)}
              />
            </Field>
          )}
          {draft.type === "telegram" && (
            <>
              <ol className="channel-helper">
                <li>Create a Telegram Bot using @BotFather.</li>
                <li>Copy the Bot Token.</li>
                <li>Add the bot to your group.</li>
                <li>Obtain the group Chat ID.</li>
                <li>Enter both credentials and save.</li>
                <li>Use Test on the saved channel.</li>
              </ol>
              <Field
                label="Bot Token"
                hint="Create a bot using @BotFather. Leave blank on edit to retain it."
              >
                <Input
                  required={!draft.id}
                  type="password"
                  autoComplete="new-password"
                  value={draft.botToken}
                  onChange={(e) => update("botToken", e.target.value)}
                />
              </Field>
              <Field
                label="Chat ID"
                hint="Add the bot to your group, then obtain its chat ID."
              >
                <Input
                  required={!draft.id}
                  type="password"
                  value={draft.chatId}
                  onChange={(e) => update("chatId", e.target.value)}
                />
              </Field>
            </>
          )}
          {draft.type === "webhook" && (
            <>
              <Field
                label="HTTPS URL"
                hint="Private, loopback, link-local, and metadata destinations are blocked."
              >
                <Input
                  required={!draft.id}
                  type="password"
                  value={draft.url}
                  onChange={(e) => update("url", e.target.value)}
                />
              </Field>
              <Field
                label="Authorization header"
                hint="Optional. Leave blank on edit to retain it."
              >
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={draft.authorization}
                  onChange={(e) => update("authorization", e.target.value)}
                />
              </Field>
              <Field label="Custom headers (JSON)">
                <textarea
                  rows={3}
                  value={draft.headers}
                  onChange={(e) => update("headers", e.target.value)}
                  placeholder={'{"X-Service": "cluecdc"}'}
                />
              </Field>
            </>
          )}
          <div className="dialog-actions">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button disabled={saving}>
              {saving ? "Saving…" : "Save channel"}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

type RuleDraft = {
  id?: string;
  name: string;
  description: string;
  enabled: boolean;
  severity: string;
  eventTypes: string[];
  sourceFilters: string[];
  pipelineFilters: string[];
  connectorFilters: string[];
  channelIds: string[];
  cooldown: string;
  policy: string;
  recovery: boolean;
};
const emptyRule: RuleDraft = {
  name: "",
  description: "",
  enabled: true,
  severity: "critical",
  eventTypes: [],
  sourceFilters: [],
  pipelineFilters: [],
  connectorFilters: [],
  channelIds: [],
  cooldown: "900",
  policy: "notify_after_cooldown",
  recovery: true,
};

export function AlertRulesPage() {
  const client = useQueryClient();
  const [editor, setEditor] = useState<RuleDraft | null>(null);
  const rules = useQuery({
    queryKey: ["alert-rules"],
    queryFn: () => api<AlertRule[]>("/alert-rules"),
  });
  const channels = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => api<NotificationChannel[]>("/notification-channels"),
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
  });
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => api<Source[]>("/sources"),
  });
  const connectors = useQuery({
    queryKey: ["connectors"],
    queryFn: () => api<Connector[]>("/connect/connectors"),
  });
  const save = useMutation({
    mutationFn: (draft: RuleDraft) =>
      api(draft.id ? `/alert-rules/${draft.id}` : "/alert-rules", {
        method: draft.id ? "PUT" : "POST",
        body: JSON.stringify({
          name: draft.name,
          description: draft.description,
          enabled: draft.enabled,
          severity: draft.severity || null,
          event_types: draft.eventTypes,
          source_filters: draft.sourceFilters,
          pipeline_filters: draft.pipelineFilters,
          connector_filters: draft.connectorFilters,
          channel_ids: draft.channelIds,
          cooldown_seconds: Number(draft.cooldown),
          notification_policy: draft.policy,
          send_recovery: draft.recovery,
        }),
      }),
    onSuccess: () => {
      setEditor(null);
      client.invalidateQueries({ queryKey: ["alert-rules"] });
      toast.success("Alert rule saved");
    },
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/alert-rules/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["alert-rules"] }),
  });
  return (
    <>
      <PageHeader
        title="Alert rules"
        description="Route normalized infrastructure events to one or more notification channels."
        eyebrow="ALERTS / RULES"
      >
        <Button asChild variant="outline">
          <Link href="/alerts">Alert history</Link>
        </Button>
        <Button onClick={() => setEditor({ ...emptyRule })}>
          <Plus size={15} /> Create rule
        </Button>
      </PageHeader>
      {rules.isPending ? (
        <Loading />
      ) : rules.isError ? (
        <ErrorPanel error={rules.error} />
      ) : rules.data.length ? (
        <DataTable
          data={rules.data}
          columns={[
            {
              accessorKey: "name",
              header: "Rule",
              cell: ({ row }) => (
                <div className="alert-title-cell">
                  <strong>{row.original.name}</strong>
                  <small>{row.original.description}</small>
                </div>
              ),
            },
            {
              accessorKey: "enabled",
              header: "Status",
              cell: ({ row }) => (
                <Status value={row.original.enabled ? "ENABLED" : "DISABLED"} />
              ),
            },
            {
              accessorKey: "severity",
              header: "Severity",
              cell: ({ row }) => (
                <Status value={row.original.severity || "ANY"} />
              ),
            },
            {
              accessorKey: "event_types",
              header: "Events",
              cell: ({ row }) =>
                row.original.event_types.length
                  ? `${row.original.event_types.length} selected`
                  : "All events",
            },
            {
              accessorKey: "channel_ids",
              header: "Channels",
              cell: ({ row }) => row.original.channel_ids.length,
            },
            {
              accessorKey: "cooldown_seconds",
              header: "Cooldown",
              cell: ({ row }) =>
                `${Math.round(row.original.cooldown_seconds / 60)} min`,
            },
            {
              id: "actions",
              header: "Actions",
              cell: ({ row }) => (
                <div className="row-actions">
                  <Button
                    variant="ghost"
                    onClick={() =>
                      setEditor({
                        id: row.original.id,
                        name: row.original.name,
                        description: row.original.description,
                        enabled: row.original.enabled,
                        severity: row.original.severity || "",
                        eventTypes: row.original.event_types,
                        sourceFilters: row.original.source_filters,
                        pipelineFilters: row.original.pipeline_filters,
                        connectorFilters: row.original.connector_filters,
                        channelIds: row.original.channel_ids,
                        cooldown: String(row.original.cooldown_seconds),
                        policy: row.original.notification_policy,
                        recovery: row.original.send_recovery,
                      })
                    }
                  >
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    aria-label={`Delete ${row.original.name}`}
                    onClick={() =>
                      confirm(`Delete ${row.original.name}?`) &&
                      remove.mutate(row.original.id)
                    }
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              ),
            },
          ]}
        />
      ) : (
        <Empty
          title="No alert rules"
          description="Create a rule to route infrastructure incidents to notification channels."
          onAction={() => setEditor({ ...emptyRule })}
          action="Create alert rule"
        />
      )}
      <RuleDialog
        key={editor?.id || (editor ? "new" : "closed")}
        draft={editor}
        channels={channels.data || []}
        pipelines={pipelines.data || []}
        sources={sources.data || []}
        connectors={connectors.data || []}
        saving={save.isPending}
        onClose={() => setEditor(null)}
        onSave={(value) => save.mutate(value)}
      />
    </>
  );
}

function RuleDialog({
  draft,
  channels,
  pipelines,
  sources,
  connectors,
  saving,
  onClose,
  onSave,
}: {
  draft: RuleDraft | null;
  channels: NotificationChannel[];
  pipelines: Pipeline[];
  sources: Source[];
  connectors: Connector[];
  saving: boolean;
  onClose: () => void;
  onSave: (draft: RuleDraft) => void;
}) {
  const [value, setValue] = useState(draft);
  const patch = (next: Partial<RuleDraft>) =>
    setValue((current) => (current ? { ...current, ...next } : current));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (value) onSave(value);
  };
  return (
    <Dialog
      open={!!draft}
      onOpenChange={(open) => !open && onClose()}
      title={draft?.id ? "Edit alert rule" : "Create alert rule"}
      description="Define conditions, destinations, cooldown, and recovery behavior."
    >
      {value && (
        <form onSubmit={submit} className="rule-form">
          <div className="form-grid">
            <Field label="Name">
              <Input
                required
                value={value.name}
                onChange={(e) => patch({ name: e.target.value })}
              />
            </Field>
            <Field label="Severity">
              <select
                value={value.severity}
                onChange={(e) => patch({ severity: e.target.value })}
              >
                <option value="">Any severity</option>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
              </select>
            </Field>
          </div>
          <Field label="Description">
            <Input
              value={value.description}
              onChange={(e) => patch({ description: e.target.value })}
            />
          </Field>
          <Field
            label="Event types"
            hint="Select one or more; an empty selection matches all events."
          >
            <div className="option-grid">
              {EVENT_TYPES.map((type) => (
                <label key={type}>
                  <input
                    type="checkbox"
                    checked={value.eventTypes.includes(type)}
                    onChange={(e) =>
                      patch({
                        eventTypes: e.target.checked
                          ? [...value.eventTypes, type]
                          : value.eventTypes.filter((item) => item !== type),
                      })
                    }
                  />{" "}
                  {type.replaceAll("_", " ")}
                </label>
              ))}
            </div>
          </Field>
          <Field label="Notification channels">
            <div className="option-grid compact">
              {channels.length ? (
                channels.map((channel) => (
                  <label key={channel.id}>
                    <input
                      type="checkbox"
                      checked={value.channelIds.includes(channel.id)}
                      onChange={(e) =>
                        patch({
                          channelIds: e.target.checked
                            ? [...value.channelIds, channel.id]
                            : value.channelIds.filter(
                                (item) => item !== channel.id,
                              ),
                        })
                      }
                    />{" "}
                    {channel.name} <small>({channel.type})</small>
                  </label>
                ))
              ) : (
                <Link className="text-link" href="/alerts/channels">
                  Add a notification channel first
                </Link>
              )}
            </div>
          </Field>
          <div className="form-grid">
            <Field label="Pipelines" hint="Empty matches every pipeline.">
              <select
                multiple
                value={value.pipelineFilters}
                onChange={(event) =>
                  patch({
                    pipelineFilters: Array.from(
                      event.currentTarget.selectedOptions,
                      (option) => option.value,
                    ),
                  })
                }
              >
                {pipelines.map((pipeline) => (
                  <option key={pipeline.id} value={pipeline.id}>
                    {pipeline.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Sources" hint="Empty matches every source.">
              <select
                multiple
                value={value.sourceFilters}
                onChange={(event) =>
                  patch({
                    sourceFilters: Array.from(
                      event.currentTarget.selectedOptions,
                      (option) => option.value,
                    ),
                  })
                }
              >
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="Connectors" hint="Empty matches every connector.">
            <select
              multiple
              value={value.connectorFilters}
              onChange={(event) =>
                patch({
                  connectorFilters: Array.from(
                    event.currentTarget.selectedOptions,
                    (option) => option.value,
                  ),
                })
              }
            >
              {connectors.map((connector) => (
                <option key={connector.id} value={connector.name}>
                  {connector.name}
                </option>
              ))}
            </select>
          </Field>
          <div className="form-grid">
            <Field label="Cooldown">
              <select
                value={value.cooldown}
                onChange={(e) => patch({ cooldown: e.target.value })}
              >
                <option value="300">5 minutes</option>
                <option value="900">15 minutes</option>
                <option value="1800">30 minutes</option>
                <option value="3600">1 hour</option>
              </select>
            </Field>
            <Field label="Notification policy">
              <select
                value={value.policy}
                onChange={(e) => patch({ policy: e.target.value })}
              >
                <option value="notify_after_cooldown">After cooldown</option>
                <option value="notify_every_occurrence">
                  Every occurrence
                </option>
                <option value="notify_first_occurrence_only">
                  First occurrence only
                </option>
              </select>
            </Field>
          </div>
          <div className="switch-row">
            <label>
              <input
                type="checkbox"
                checked={value.recovery}
                onChange={(e) => patch({ recovery: e.target.checked })}
              />{" "}
              Send recovery notification
            </label>
            <label>
              <input
                type="checkbox"
                checked={value.enabled}
                onChange={(e) => patch({ enabled: e.target.checked })}
              />{" "}
              Rule enabled
            </label>
          </div>
          <div className="dialog-actions">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button disabled={saving}>
              <CheckCircle2 size={15} /> {saving ? "Saving…" : "Save rule"}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
