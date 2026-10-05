"use client";

import { ErrorPanel, Loading, PageHeader, Status } from "@/components/common";
import { api, date, post } from "@/lib/api";
import type { AlertDetail } from "@cluecdc/contracts";
import { Button } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { invalidateAlerts } from "./shared";

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
