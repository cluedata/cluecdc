"use client";

import {
  DataTable,
  Empty,
  ErrorPanel,
  Loading,
  PageHeader,
  Status,
  Tabs,
} from "@/components/common";
import { api, relativeTime } from "@/lib/api";
import type { AlertPage, Pipeline } from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { EVENT_TYPES } from "./shared";

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
