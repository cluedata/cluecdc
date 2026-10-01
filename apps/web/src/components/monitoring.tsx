"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Database,
  GitBranch,
  Radio,
  AlertCircle,
} from "lucide-react";
import type {
  Overview,
  Pipeline,
  Destination,
  Connector,
} from "@cluecdc/contracts";
import { api, number } from "@/lib/api";
import { DataTable, ErrorPanel, Loading, PageHeader, Status } from "./common";
import { MetricCard, Panel, QuietState } from "./operational";
import { MetricsHistory } from "./overview";

function failedTasks(c: Connector) {
  const runtime = c.runtime_json;
  if (
    !runtime ||
    typeof runtime !== "object" ||
    Array.isArray(runtime) ||
    !Array.isArray(runtime.tasks)
  )
    return null;
  return runtime.tasks.filter(
    (t) =>
      t && typeof t === "object" && !Array.isArray(t) && t.state === "FAILED",
  ).length;
}
export function MonitoringPage() {
  const o = useQuery({
    queryKey: ["overview"],
    queryFn: () => api<Overview>("/monitoring/overview"),
    refetchInterval: 15000,
  });
  const p = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
    refetchInterval: 15000,
  });
  const d = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api<Destination[]>("/destinations"),
    refetchInterval: 15000,
  });
  const c = useQuery({
    queryKey: ["connectors"],
    queryFn: () => api<Connector[]>("/connect/connectors"),
    refetchInterval: 15000,
  });
  const failures =
    c.data?.filter(
      (c) => c.actual_state === "FAILED" || (failedTasks(c) || 0) > 0,
    ) || [];
  return (
    <>
      <PageHeader
        title="Monitoring"
        description="Platform, capture and delivery health with runtime failures and metrics availability."
        eyebrow="OPERATIONS / MONITORING"
      />
      {o.isPending ? (
        <Loading />
      ) : o.isError ? (
        <ErrorPanel error={o.error} />
      ) : (
        <div className="resource-summary">
          <MetricCard
            label="Healthy Kafka clusters"
            value={`${o.data.healthy_kafka_clusters}/${o.data.kafka_clusters}`}
            detail="Last checked cluster health"
            icon={Radio}
            href="/kafka/clusters"
          />
          <MetricCard
            label="Healthy Connect clusters"
            value={`${o.data.healthy_connect_clusters}/${o.data.connect_clusters}`}
            detail="Last checked worker health"
            icon={Activity}
            href="/connect/clusters"
          />
          <MetricCard
            label="Capture running"
            value={`${o.data.running}/${o.data.pipelines}`}
            detail={`${o.data.failed + o.data.degraded} require attention`}
            icon={GitBranch}
            href="/pipelines"
          />
          <MetricCard
            label="Delivery running"
            value={`${o.data.destination_running}/${o.data.destinations}`}
            detail={`${o.data.destination_failed + o.data.destination_degraded} require attention`}
            icon={Database}
            href="/destinations"
          />
        </div>
      )}
      <div className="dashboard-primary monitoring-panels">
        <Panel title="Capture health">
          {p.isPending ? (
            <Loading />
          ) : p.isError ? (
            <ErrorPanel error={p.error} />
          ) : !p.data.length ? (
            <QuietState
              icon={GitBranch}
              title="No capture pipelines"
              description="Create and deploy a capture pipeline to monitor its runtime."
            />
          ) : (
            <DataTable
              getRowHref={(p) => `/pipelines/${p.id}`}
              data={p.data}
              columns={[
                { accessorKey: "name", header: "Pipeline" },
                {
                  accessorKey: "actual_state",
                  header: "Capture state",
                  cell: ({ row }) => (
                    <Status value={row.original.actual_state} />
                  ),
                },
                {
                  accessorKey: "delivery_state",
                  header: "Delivery",
                  cell: ({ row }) => (
                    <Status value={row.original.delivery_state} />
                  ),
                },
                {
                  accessorKey: "cdc_lag",
                  header: "CDC lag",
                  cell: ({ row }) => number(row.original.cdc_lag),
                },
              ]}
            />
          )}
        </Panel>
        <Panel title="Delivery health">
          {d.isPending ? (
            <Loading />
          ) : d.isError ? (
            <ErrorPanel error={d.error} />
          ) : !d.data.length ? (
            <QuietState
              title="No destinations"
              description="Configure deliveries to monitor downstream runtime health."
            />
          ) : (
            <DataTable
              getRowHref={(d) => `/destinations/${d.id}`}
              data={d.data}
              columns={[
                { accessorKey: "name", header: "Destination" },
                {
                  accessorKey: "actual_state",
                  header: "Delivery state",
                  cell: ({ row }) => (
                    <Status value={row.original.actual_state} />
                  ),
                },
                { accessorKey: "connected_pipelines", header: "Pipelines" },
                {
                  accessorKey: "delivery_lag",
                  header: "Delivery lag",
                  cell: ({ row }) => number(row.original.delivery_lag),
                },
              ]}
            />
          )}
        </Panel>
      </div>
      <div className="dashboard-primary monitoring-panels">
        <MetricsHistory />
        <Panel
          title="Connector & task failures"
          actions={
            <Link className="text-link" href="/operations/errors">
              Open Error Center
            </Link>
          }
        >
          {c.isPending ? (
            <Loading />
          ) : c.isError ? (
            <ErrorPanel error={c.error} />
          ) : !failures.length ? (
            <QuietState
              icon={AlertCircle}
              title="No observed task failures"
              description="Failed connectors and tasks appear here after runtime reconciliation."
            />
          ) : (
            <DataTable
              data={failures}
              getRowHref={(c) =>
                `/connect/connectors?connector=${encodeURIComponent(c.name)}`
              }
              columns={[
                { accessorKey: "name", header: "Connector" },
                { accessorKey: "connector_type", header: "Direction" },
                {
                  accessorKey: "actual_state",
                  header: "State",
                  cell: ({ row }) => (
                    <Status value={row.original.actual_state} />
                  ),
                },
                {
                  id: "tasks",
                  header: "Failed tasks",
                  accessorFn: (c) => failedTasks(c) ?? "Unavailable",
                },
              ]}
            />
          )}
        </Panel>
      </div>
      <p className="muted">
        Runtime observations refresh every 15 seconds. Historical throughput,
        event freshness and lag require a metrics provider.
      </p>
    </>
  );
}
