"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, date } from "@/lib/api";
import {
  evidenceText,
  type MetricEvidence,
  type Observation,
  type ObservationHistory,
} from "@/lib/observability";
import { DataTable, ErrorPanel, Loading, Status } from "./common";
import { Panel } from "./operational";

export function Evidence({ metric }: { metric: MetricEvidence }) {
  return (
    <span
      title={`${metric.reason_code}; ${metric.source}; ${metric.confidence}; observed ${date(metric.observed_at)}`}
    >
      <strong>{evidenceText(metric)}</strong>
      <small className="muted">
        {" "}
        · {metric.confidence.toLowerCase()} · {metric.source} ·{" "}
        {date(metric.observed_at)}
      </small>
    </span>
  );
}

export function ObservabilityOverview() {
  const [provider, setProvider] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["observability", provider, status, offset],
    queryFn: () =>
      api<Observation[]>(
        `/observability/summary?limit=100&offset=${offset}&provider=${provider}&status=${status}`,
      ),
    refetchInterval: 15000,
  });
  const rows = query.data || [];
  return (
    <Panel
      title="CDC observability"
      description="Persisted measurements. Connector RUNNING does not establish synchronization."
    >
      <div className="filter-bar">
        <label>
          Source provider{" "}
          <select
            aria-label="Observability source provider"
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All providers</option>
            {[
              "postgresql",
              "mysql",
              "mariadb",
              "sqlserver",
              "mongodb",
              "oracle",
            ].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label>
          Pipeline status{" "}
          <select
            aria-label="Observability pipeline status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All states</option>
            {[
              "RUNNING",
              "PAUSED",
              "FAILED",
              "DEGRADED",
              "STOPPED",
              "UNKNOWN",
            ].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted">
        On this page:{" "}
        {
          rows.filter((r) => r.findings.some((f) => f.code.endsWith("STALLED")))
            .length
        }{" "}
        stalled · {rows.filter((r) => r.status !== "AVAILABLE").length}{" "}
        incomplete
      </p>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} />
      ) : (
        <DataTable
          data={rows}
          getRowHref={(r) => `/pipelines/${r.pipeline_id}`}
          columns={[
            { accessorKey: "name", header: "Pipeline" },
            { accessorKey: "source_provider", header: "Source" },
            {
              accessorKey: "connector_state",
              header: "Capture state",
              cell: ({ row }) => (
                <Status value={row.original.connector_state} />
              ),
            },
            {
              id: "lag",
              header: "Capture lag",
              cell: ({ row }) => evidenceText(row.original.capture_lag),
            },
            {
              id: "backlog",
              header: "Backlog by delivery",
              cell: ({ row }) =>
                row.original.deliveries.length
                  ? row.original.deliveries.map((d) => (
                      <div key={d.delivery_id}>
                        {d.name}: {evidenceText(d.backlog)}
                      </div>
                    ))
                  : "Unavailable",
            },
            {
              id: "snapshot",
              header: "Snapshots",
              cell: ({ row }) =>
                row.original.snapshots
                  .map((s) => `${s.table}: ${s.state}`)
                  .join(", ") || "Insufficient evidence",
            },
            {
              id: "finding",
              header: "Diagnostic",
              cell: ({ row }) =>
                row.original.findings.map((f) => f.code).join(", ") ||
                "Insufficient evidence",
            },
            { accessorKey: "status", header: "Telemetry" },
            {
              id: "last",
              header: "Last successful collection",
              cell: ({ row }) => date(row.original.last_success_at),
            },
          ]}
        />
      )}
      <div className="filter-bar">
        <button
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 100))}
        >
          Previous
        </button>
        <button
          disabled={rows.length < 100}
          onClick={() => setOffset(offset + 100)}
        >
          Next
        </button>
      </div>
    </Panel>
  );
}

export function CaptureTrend({ history }: { history: ObservationHistory }) {
  const points = history.points;
  const values = points.map((p) =>
    p.capture_lag.status === "AVAILABLE" ? p.capture_lag.value : null,
  );
  const max = Math.max(1, ...values.filter((v): v is number => v !== null));
  const start = points.length ? Date.parse(points[0].collected_at) : 0;
  const width = Math.max(
    1,
    points.length
      ? Date.parse(points[points.length - 1].collected_at) - start
      : 1,
  );
  const x = (i: number) =>
    10 + ((Date.parse(points[i].collected_at) - start) / width) * 580;
  const y = (v: number) => 140 - (v / max) * 120;
  return (
    <>
      {values.some((v) => v !== null) ? (
        <svg
          viewBox="0 0 600 160"
          role="img"
          aria-label="Capture lag history in milliseconds"
          style={{ width: "100%", maxHeight: 220 }}
        >
          <line
            x1="10"
            x2="590"
            y1="140"
            y2="140"
            stroke="currentColor"
            opacity="0.3"
          />
          {values.map((value, i) =>
            value === null ? null : (
              <g key={points[i].collected_at}>
                <circle cx={x(i)} cy={y(value)} r="3" fill="currentColor">
                  <title>
                    {date(points[i].collected_at)}: {value} ms
                  </title>
                </circle>
                {i > 0 &&
                values[i - 1] !== null &&
                !history.gaps.some(
                  (g) =>
                    Date.parse(g.start) < Date.parse(points[i].collected_at) &&
                    Date.parse(g.end) > Date.parse(points[i - 1].collected_at),
                ) ? (
                  <line
                    x1={x(i - 1)}
                    y1={y(values[i - 1]!)}
                    x2={x(i)}
                    y2={y(value)}
                    stroke="currentColor"
                  />
                ) : null}
              </g>
            ),
          )}
        </svg>
      ) : (
        <p>Unavailable: no measured capture lag in this range.</p>
      )}
      <p className="muted">
        {history.gaps.length} explicit sample gaps.{" "}
        {history.truncated ? "Point limit reached; narrow the time range." : ""}
      </p>
      <details>
        <summary>Historical measurements and gaps</summary>
        <ul>
          {points.map((p) => (
            <li key={p.collected_at}>
              {date(p.collected_at)}: {evidenceText(p.capture_lag)}
            </li>
          ))}
          {history.gaps.map((g) => (
            <li key={g.start}>
              Gap: {date(g.start)} – {date(g.end)} ({g.reason_code})
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}

export function PipelineObservability({ id }: { id: string }) {
  const [hours, setHours] = useState("1");
  const query = useQuery({
    queryKey: ["pipeline-observability", id],
    queryFn: () => api<Observation>(`/pipelines/${id}/observability`),
    refetchInterval: 15000,
  });
  const history = useQuery({
    queryKey: ["observability-history", id, hours],
    queryFn: () => {
      const end = new Date();
      const start = new Date(end.getTime() - Number(hours) * 3600000 + 5000);
      return api<ObservationHistory>(
        `/pipelines/${id}/observability/history?start=${start.toISOString()}&end=${end.toISOString()}`,
      );
    },
    refetchInterval: 60000,
  });
  if (query.isPending) return <Loading />;
  if (query.isError) return <ErrorPanel error={query.error} />;
  const sample = query.data;
  return (
    <div className="observability-details">
      <Panel
        title="Capture evidence"
        description="Debezium processing delay includes clock differences and does not measure destination latency."
      >
        <p>
          Telemetry: <Status value={sample.status} /> · Collected{" "}
          {date(sample.collected_at)} · Last successful collection{" "}
          {date(sample.last_success_at)}
        </p>
        <p>
          Capture lag: <Evidence metric={sample.capture_lag} />
        </p>
        <p>
          Captured events: <Evidence metric={sample.capture_events} />
        </p>
      </Panel>
      {sample.deliveries.map((d) => (
        <Panel key={d.delivery_id} title={`Delivery: ${d.name}`}>
          <p>
            Connector: <Status value={d.connector_state} /> · Group{" "}
            {d.group_id || "Unavailable"}:{" "}
            {d.group_state || "Insufficient evidence"}
          </p>
          <p>
            Consumer backlog: <Evidence metric={d.backlog} /> · Partition
            coverage {d.valid_partitions}/{d.required_partitions}
          </p>
          <h3>Sink processing and flush evidence</h3>
          <p className="muted">
            Delivery state: {d.operational_state} · Tasks:{" "}
            {Object.entries(d.task_states)
              .map(([id, state]) => `${id}: ${state}`)
              .join(", ") || "Unknown"}
          </p>
          <p className="muted">
            Verified task workers: {d.verified_task_workers}/{d.required_tasks}
          </p>
          {Object.entries(d.sink_metrics).map(([key, m]) => (
            <p key={key}>
              {key.replaceAll("_", " ")}: <Evidence metric={m} />
            </p>
          ))}
          {!Object.keys(d.sink_metrics).length && (
            <p>Unavailable: task metrics were not collected.</p>
          )}
          <h3>Destination applied or visible evidence</h3>
          <Evidence metric={d.destination_progress} />
          <p className="muted">
            Consumer commits alone do not verify destination writes. Warehouse
            evidence covers only the recorded staged batch.
          </p>
        </Panel>
      ))}
      <Panel title="Snapshot evidence">
        {!sample.snapshots.length ? (
          <p>Insufficient evidence</p>
        ) : (
          sample.snapshots.map((s) => (
            <div key={s.table_id}>
              <h3>
                {s.table} · {s.kind} · {s.state}
                {s.stale ? " · Stale" : ""}
              </h3>
              <p>
                Rows: <Evidence metric={s.processed_rows} /> · Estimated{" "}
                {s.estimated_rows ?? "Unavailable"} · Percentage{" "}
                {s.percentage === null
                  ? "Insufficient evidence"
                  : `${s.percentage}%`}
              </p>
              <p>
                Started {date(s.started_at)} · Last progress{" "}
                {date(s.last_progress_at)} · Completed {date(s.completed_at)} ·
                Chunk {s.current_chunk ?? "Unavailable"} · {s.provenance}
              </p>
            </div>
          ))
        )}
      </Panel>
      <Panel title="Diagnostics">
        {sample.findings.length ? (
          sample.findings.map((f, i) => (
            <article key={`${f.code}-${i}`}>
              <h3>
                {f.code} · {f.severity}
              </h3>
              <p>{f.explanation}</p>
              <p>{f.suggested_action}</p>
              <p className="muted">
                {f.confidence} · {date(f.observed_at)}
                {f.delivery_id ? ` · Delivery ${f.delivery_id}` : ""}
              </p>
              <ul>
                {f.observations.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </article>
          ))
        ) : (
          <p>Insufficient evidence for a current diagnostic finding.</p>
        )}
      </Panel>
      <Panel
        title="Capture lag history"
        actions={
          <label>
            Time range{" "}
            <select
              aria-label="Observability time range"
              value={hours}
              onChange={(e) => setHours(e.target.value)}
            >
              <option value="1">1 hour</option>
              <option value="24">24 hours</option>
              <option value="168">7 days</option>
            </select>
          </label>
        }
      >
        {history.isPending ? (
          <Loading />
        ) : history.isError ? (
          <ErrorPanel error={history.error} />
        ) : (
          <CaptureTrend history={history.data} />
        )}
      </Panel>
      <Link className="text-link" href="/monitoring">
        Monitoring overview
      </Link>
    </div>
  );
}
