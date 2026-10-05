"use client";

import {
  DataTable,
  Empty,
  ErrorPanel,
  Field,
  Loading,
  PageHeader,
  Status,
} from "@/components/common";
import { api } from "@/lib/api";
import type {
  AlertRule,
  Connector,
  NotificationChannel,
  Pipeline,
  Source,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { EVENT_TYPES } from "./shared";

export type RuleDraft = {
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

export const emptyRule: RuleDraft = {
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

export function RuleDialog({
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
