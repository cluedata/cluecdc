"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@cluecdc/ui";
import {
  Empty,
  ErrorPanel,
  JsonView,
  Loading,
  PageHeader,
  Status,
} from "@/components/common";
import { api, date, post } from "@/lib/api";
import { useAuthorization } from "@/lib/auth";
import { Check, Page, Result, Run, label, ruleNames } from "./types";

const base = "/data-quality";

function qualityDate(value: string | null | undefined, zone = "UTC") {
  if (!value) return "Never";
  try {
    return (
      new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: zone,
      }).format(new Date(value)) + ` (${zone})`
    );
  } catch {
    return date(value) + " (browser timezone)";
  }
}

function Measurement({ value }: { value: unknown }) {
  if (value && typeof value === "object" && "value" in value)
    return <>{String(value.value ?? "NULL")}</>;
  return (
    <>{value === null || value === undefined ? "Unavailable" : String(value)}</>
  );
}

function ResultEvidence({ result }: { result: Result }) {
  const evidence = result.evidence_json;
  return (
    <article>
      <h3>
        {label(result.rule_type)} · <Status value={result.quality} />
      </h3>
      <div className="dq-grid">
        <p>
          Source:{" "}
          <Measurement value={evidence.source ?? evidence.source_columns} />
        </p>
        <p>
          Destination:{" "}
          <Measurement
            value={evidence.destination ?? evidence.destination_columns}
          />
        </p>
      </div>
      {Object.entries(evidence)
        .filter(([key]) =>
          [
            "signed_difference",
            "absolute_difference",
            "percentage_difference",
            "difference_seconds",
            "signed_delay_seconds",
            "destination_age_seconds",
            "error_code",
          ].includes(key),
        )
        .map(([key, value]) => (
          <p key={key}>
            {label(key)}: {String(value ?? "Infinite relative difference")}
          </p>
        ))}
      <details>
        <summary>Measurements, thresholds & consistency evidence</summary>
        <JsonView value={evidence} />
      </details>
    </article>
  );
}

function LatestResults({ run }: { run: Run }) {
  const query = useQuery({
    queryKey: ["data-quality", "run", run.id],
    queryFn: () => api<Run>(`${base}/runs/${run.id}`),
    refetchInterval: ["PENDING", "RUNNING"].includes(run.status) ? 2000 : false,
  });
  return (
    <section className="panel dq-panel">
      <h2>
        Latest result: <Status value={run.quality || run.status} />
      </h2>
      <Link href={`/data-quality/runs/${run.id}`}>View run details</Link>
      {query.error ? (
        <ErrorPanel error={query.error} />
      ) : query.isLoading ? (
        <Loading />
      ) : (
        query.data?.results?.map((r) => (
          <ResultEvidence key={r.id} result={r} />
        ))
      )}
    </section>
  );
}
export function Confidence() {
  return (
    <p className="dq-notice">
      <strong>BEST_EFFORT</strong> · Independent read-only observations may race
      with writes. PASS means the configured condition passed; it does not
      certify CDC completeness.
    </p>
  );
}

function Actions({ item }: { item: Check }) {
  const { can } = useAuthorization();
  const queryClient = useQueryClient();
  const router = useRouter();
  const action = useMutation({
    mutationFn: (name: string) =>
      name === "delete"
        ? api(`${base}/checks/${item.id}`, { method: "DELETE" })
        : post<Run>(
            `${base}/checks/${item.id}/${name}`,
            name === "run" ? { request_key: crypto.randomUUID() } : undefined,
          ),
    onSuccess: (result, name) => {
      queryClient.invalidateQueries({ queryKey: ["data-quality"] });
      if (
        name === "run" &&
        result &&
        typeof result === "object" &&
        "id" in result
      )
        router.push(`/data-quality/runs/${result.id}`);
      if (name === "delete") router.push("/data-quality");
    },
  });
  return (
    <div className="dq-actions">
      {can("data_quality.write") && (
        <>
          <Button asChild variant="outline">
            <Link href={`/data-quality/${item.id}/edit`}>Edit Check</Link>
          </Button>
          <Button
            variant="outline"
            disabled={action.isPending}
            onClick={() =>
              action.mutate(
                item.configuration.schedule.enabled ? "pause" : "resume",
              )
            }
          >
            {item.configuration.schedule.enabled
              ? "Pause Schedule"
              : "Resume Schedule"}
          </Button>
        </>
      )}
      {can("data_quality.run") && (
        <Button
          disabled={!!item.active_run_id || action.isPending}
          onClick={() => action.mutate("run")}
        >
          Run Now
        </Button>
      )}
      {can("data_quality.admin") && (
        <Button
          variant="outline"
          disabled={action.isPending}
          onClick={() => {
            if (
              window.confirm(
                `Delete ${item.name}? Execution history is retained.`,
              )
            )
              action.mutate("delete");
          }}
        >
          Delete Check
        </Button>
      )}
      {action.error && <ErrorPanel error={action.error} />}
    </div>
  );
}

export function ChecksPage() {
  const { can } = useAuthorization();
  const [offset, setOffset] = useState(0);
  const [filters, setFilters] = useState({
    enabled: "",
    source: "",
    destination: "",
    rule: "",
    quality: "",
  });
  const params = new URLSearchParams({ offset: String(offset) });
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = useQuery({
    queryKey: ["data-quality", "checks", params.toString()],
    queryFn: () => api<Page<Check>>(`${base}/checks?${params}`),
    refetchInterval: 5000,
  });
  const items = query.data?.items || [];
  return (
    <>
      <PageHeader
        title="Data Quality"
        description="Observe comparable current-state datasets across registered connections."
      >
        {can("data_quality.write") && (
          <Button asChild>
            <Link href="/data-quality/new">Create Check</Link>
          </Button>
        )}
      </PageHeader>
      <Confidence />
      <div className="dq-filters">
        {Object.entries(filters).map(([key, value]) => (
          <label key={key}>
            {label(key)}
            <select
              aria-label={`Filter ${key}`}
              value={value}
              onChange={(e) => {
                setFilters({ ...filters, [key]: e.target.value });
                setOffset(0);
              }}
            >
              <option value="">All</option>
              {key === "enabled" ? (
                <>
                  <option value="true">Enabled</option>
                  <option value="false">Disabled</option>
                </>
              ) : key === "rule" ? (
                ruleNames.map((r) => <option key={r}>{r}</option>)
              ) : key === "quality" ? (
                ["PASS", "FAIL", "INCONCLUSIVE", "ERROR"].map((s) => (
                  <option key={s}>{s}</option>
                ))
              ) : (
                [
                  ...new Set(
                    items.map(
                      (i) =>
                        i.configuration[key as "source" | "destination"]
                          .connection_id,
                    ),
                  ),
                ].map((id) => <option key={id}>{id}</option>)
              )}
            </select>
          </label>
        ))}
      </div>
      {query.isLoading ? (
        <Loading />
      ) : query.error ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : !items.length ? (
        <Empty
          title="No Data Quality checks"
          description="Create a check using a Source and a Destination. A CDC pipeline is optional."
        />
      ) : (
        <div className="dq-table">
          <table>
            <thead>
              <tr>
                {[
                  "Check",
                  "Source → Destination",
                  "Rules",
                  "Schedule",
                  "Latest result",
                  "Last run / Next run",
                  "Alerting",
                  "Actions",
                ].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link href={`/data-quality/${item.id}`}>{item.name}</Link>
                  </td>
                  <td>
                    <strong>{item.source_name}</strong>
                    <br />
                    {item.configuration.source.schema_name}.
                    {item.configuration.source.table}
                    <br />→ <strong>{item.destination_name}</strong>
                    <br />
                    {item.configuration.destination.schema_name}.
                    {item.configuration.destination.table}
                  </td>
                  <td>{item.configuration.rules.length}</td>
                  <td>
                    {item.enabled
                      ? item.configuration.schedule.enabled
                        ? `Every ${item.configuration.schedule.frequency_seconds / 60} minutes`
                        : "Manual / paused"
                      : "Disabled"}
                  </td>
                  <td>
                    <Status value={item.latest_run?.quality || "NOT RUN"} />
                  </td>
                  <td>
                    {qualityDate(
                      item.latest_run?.started_at,
                      item.configuration.schedule.display_timezone,
                    )}
                    <br />
                    {qualityDate(
                      item.next_run_at,
                      item.configuration.schedule.display_timezone,
                    )}
                  </td>
                  <td>
                    {item.alert_status.configured
                      ? item.alert_status.available
                        ? item.alert_status.channel_name
                        : "Channel unavailable"
                      : "Disabled"}
                  </td>
                  <td>
                    <Link href={`/data-quality/${item.id}`}>View Results</Link>
                    <Actions item={item} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="dq-actions">
        <Button
          variant="outline"
          disabled={!offset}
          onClick={() => setOffset(Math.max(0, offset - 50))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          disabled={!query.data?.has_more}
          onClick={() => setOffset(offset + 50)}
        >
          Next
        </Button>
      </div>
    </>
  );
}

export function CheckPage({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const check = useQuery({
    queryKey: ["data-quality", id],
    queryFn: () => api<Check>(`${base}/checks/${id}`),
    refetchInterval: 3000,
  });
  const history = useQuery({
    queryKey: ["data-quality", id, "history", offset],
    queryFn: () => api<Page<Run>>(`${base}/checks/${id}/runs?offset=${offset}`),
    refetchInterval: 3000,
  });
  if (check.isLoading) return <Loading />;
  if (check.error || !check.data) return <ErrorPanel error={check.error} />;
  const item = check.data;
  return (
    <>
      <PageHeader
        title={item.name}
        description={
          item.configuration.description ||
          "Independent Source / Destination check"
        }
      >
        <Actions item={item} />
      </PageHeader>
      <Confidence />
      <section className="panel dq-panel">
        <h2>Datasets & schedule</h2>
        <p>
          {item.configuration.source.database}.
          {item.configuration.source.schema_name}.
          {item.configuration.source.table} →{" "}
          {item.configuration.destination.database}.
          {item.configuration.destination.schema_name}.
          {item.configuration.destination.table}
        </p>
        <p>
          Source connection:{" "}
          {item.source_name || item.configuration.source.connection_id}
          <br />
          Destination connection:{" "}
          {item.destination_name ||
            item.configuration.destination.connection_id}
        </p>
        <p>
          Pipeline:{" "}
          {item.configuration.pipeline_id || "No associated CDC pipeline"}
        </p>
        {item.related_pipelines?.length ? (
          <p>
            Discovered pipeline context:{" "}
            {item.related_pipelines.map((p) => (
              <Link key={p.id} href={`/pipelines/${p.id}`}>
                {p.name}{" "}
              </Link>
            ))}
            . Context does not establish a guaranteed replication boundary.
          </p>
        ) : null}
        <p>
          {item.configuration.schedule.enabled
            ? `Every ${item.configuration.schedule.frequency_seconds / 60} minutes`
            : "Manual / paused"}{" "}
          · Next run:{" "}
          {qualityDate(
            item.next_run_at,
            item.configuration.schedule.display_timezone,
          )}{" "}
          · Execution windows persist in UTC.
        </p>
        <p>
          Alerting:{" "}
          {item.alert_status.configured
            ? item.alert_status.available
              ? item.alert_status.channel_name
              : "Channel unavailable; delivery is not guaranteed"
            : "Disabled; select an enabled notification channel to activate"}
        </p>
      </section>
      {item.latest_run && <LatestResults run={item.latest_run} />}
      <section className="panel dq-panel">
        <h2>Rules</h2>
        {item.configuration.rules.map((r) => (
          <div key={r.type}>
            <h3>{label(r.type)}</h3>
            <details>
              <summary>Rule configuration</summary>
              <JsonView value={r} />
            </details>
          </div>
        ))}
      </section>
      <section className="panel dq-panel">
        <h2>Execution history</h2>
        {history.error ? (
          <ErrorPanel error={history.error} />
        ) : history.isLoading ? (
          <Loading />
        ) : !history.data?.items.length ? (
          <p>No runs yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Execution</th>
                <th>Quality</th>
                <th>Started</th>
                <th>Finished</th>
              </tr>
            </thead>
            <tbody>
              {history.data.items.map((run) => (
                <tr key={run.id}>
                  <td>
                    <Link href={`/data-quality/runs/${run.id}`}>
                      {run.trigger} · v{run.version}
                    </Link>
                  </td>
                  <td>{run.status}</td>
                  <td>
                    <Status value={run.quality || "PENDING"} />
                  </td>
                  <td>
                    {qualityDate(
                      run.started_at,
                      item.configuration.schedule.display_timezone,
                    )}
                  </td>
                  <td>
                    {qualityDate(
                      run.finished_at,
                      item.configuration.schedule.display_timezone,
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="dq-actions">
          <Button
            variant="outline"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            disabled={!history.data?.has_more}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </Button>
        </div>
      </section>
      <section className="panel dq-panel">
        <h2>Alert history</h2>
        {item.alerts?.length ? (
          item.alerts.map((a) => (
            <p key={a.id}>
              <Link href={`/alerts/${a.id}`}>{a.event_type}</Link> · {a.status}{" "}
              · {a.message}
            </p>
          ))
        ) : (
          <p>No incidents.</p>
        )}
      </section>
    </>
  );
}

export function RunPage({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ["data-quality", "run", id],
    queryFn: () => api<Run>(`${base}/runs/${id}`),
    refetchInterval: (query) =>
      ["PENDING", "RUNNING"].includes(query.state.data?.status || "PENDING")
        ? 1500
        : false,
  });
  if (query.isLoading) return <Loading />;
  if (query.error || !query.data) return <ErrorPanel error={query.error} />;
  const run = query.data;
  return (
    <>
      <PageHeader
        title="Data Quality run"
        description={`${run.trigger} · Configuration version ${run.version}`}
      >
        <Button asChild variant="outline">
          <Link href={`/data-quality/${run.check_id}`}>Back to Check</Link>
        </Button>
      </PageHeader>
      <Confidence />
      <section className="panel dq-panel">
        <h2>Execution: {run.status}</h2>
        <p>
          Quality: <Status value={run.quality || "PENDING"} />
        </p>
        <p>
          Scheduled evaluation:{" "}
          {qualityDate(
            run.scheduled_at,
            run.snapshot_json?.schedule.display_timezone,
          )}
          <br />
          Started:{" "}
          {qualityDate(
            run.started_at,
            run.snapshot_json?.schedule.display_timezone,
          )}
          <br />
          Finished:{" "}
          {qualityDate(
            run.finished_at,
            run.snapshot_json?.schedule.display_timezone,
          )}
        </p>
        <p>
          Duration:{" "}
          {run.started_at && run.finished_at
            ? `${((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(2)} seconds`
            : "Pending"}
        </p>
        <p>
          Window:{" "}
          {Object.keys(run.window_json).length
            ? `${run.window_json.start} ≤ timestamp < ${run.window_json.end}`
            : "Full eligible dataset / current schema"}
        </p>
        {run.error_code && <p role="alert">Error: {run.error_code}</p>}
      </section>
      <section className="panel dq-panel">
        <h2>Rule results</h2>
        {!run.results?.length ? (
          <p>Results will appear when execution finishes.</p>
        ) : (
          run.results.map((r) => <ResultEvidence key={r.id} result={r} />)
        )}
      </section>
    </>
  );
}
