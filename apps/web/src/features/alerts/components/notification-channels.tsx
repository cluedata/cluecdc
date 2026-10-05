"use client";

import {
  Empty,
  ErrorPanel,
  Field,
  Loading,
  PageHeader,
  Status,
} from "@/components/common";
import { api, post } from "@/lib/api";
import type { NotificationChannel } from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Send, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

export type ChannelForm = {
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

export const emptyChannel: ChannelForm = {
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

export function ChannelDialog({
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
