"use client";

import {
  DataTable,
  Empty,
  ErrorPanel,
  JsonView,
  Loading,
  PageHeader,
  Status,
  Tabs,
} from "@/components/common";
import { DataFlow } from "@/components/data-flow";
import { MetricCard } from "@/components/operational";
import { EventsExplorer } from "@/components/stream";
import { derivePipelineHealth } from "@/domain/data-flow";
import { api, date, number, post } from "@/lib/api";
import type {
  Audit,
  PipelineDetail,
  PipelineOperation,
  PipelineTable,
  Runtime,
  SchemaVersion,
  SourceTable,
} from "@cluecdc/contracts";
import { Button, Dialog } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import {
  ArrowLeft,
  GitBranch,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

export function PipelineDetailPage({ id }: { id: string }) {
  const router = useRouter();
  const client = useQueryClient();
  const [tab, setTab] = useState("Overview");
  const [confirm, setConfirm] = useState(false);
  const [addTables, setAddTables] = useState(false);
  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [tableSearch, setTableSearch] = useState("");
  const [initialDataStrategy, setInitialDataStrategy] = useState<
    "BACKFILL" | "FUTURE_ONLY"
  >("BACKFILL");
  const [destinationHandling, setDestinationHandling] = useState<
    "AUTO_CREATE" | "USE_EXISTING" | "VALIDATE_ONLY"
  >("AUTO_CREATE");
  const [tableAction, setTableAction] = useState<{
    type: "resync" | "stop" | "remove";
    table: PipelineTable;
  } | null>(null);
  const [deleteDestinationData, setDeleteDestinationData] = useState(false);
  const [confirmDestinationDelete, setConfirmDestinationDelete] =
    useState(false);
  const query = useQuery({
    queryKey: ["pipeline", id],
    queryFn: () => api<PipelineDetail>(`/pipelines/${id}`),
    refetchInterval: 10000,
  });
  const status = useQuery({
    queryKey: ["pipeline-status", id],
    queryFn: () => api<Runtime>(`/pipelines/${id}/status`),
    refetchInterval: 10000,
  });
  const activity = useQuery({
    queryKey: ["audit", id],
    queryFn: () => api<Audit[]>(`/audit?resource_id=${id}`),
    enabled: tab === "Activity",
  });
  const schemas = useQuery({
    queryKey: ["schemas", query.data?.source_id],
    queryFn: () =>
      api<SchemaVersion[]>(`/data/schemas?source_id=${query.data?.source_id}`),
    enabled: tab === "Schema" && !!query.data,
  });
  const availableTables = useQuery({
    queryKey: ["source-tables", query.data?.source_id, tableSearch],
    queryFn: () =>
      api<SourceTable[]>(
        `/sources/${query.data?.source_id}/tables?search=${encodeURIComponent(tableSearch)}`,
      ),
    enabled: addTables && !!query.data,
  });
  const operations = useQuery({
    queryKey: ["pipeline-operations", id],
    queryFn: () => api<PipelineOperation[]>(`/pipelines/${id}/operations`),
    enabled: tab === "Operations",
    refetchInterval: 3000,
  });
  const retryOperation = useMutation({
    mutationFn: (operationId: string) =>
      post<PipelineOperation>(`/operations/${operationId}/retry`),
    onSuccess: () => {
      toast.success("Operation retry queued");
      client.invalidateQueries({ queryKey: ["pipeline-operations", id] });
      client.invalidateQueries({ queryKey: ["pipeline", id] });
    },
    onError: (error) => toast.error(error.message),
  });
  const operation = useMutation({
    mutationFn: (op: string) =>
      op === "delete"
        ? api(`/pipelines/${id}`, { method: "DELETE" })
        : post(`/pipelines/${id}/${op}`),
    onSuccess: (_, op) => {
      toast.success(`Pipeline ${op} request completed`);
      if (op === "delete") router.push("/pipelines");
      else {
        client.invalidateQueries({ queryKey: ["pipeline"] });
        client.invalidateQueries({ queryKey: ["pipeline-status"] });
      }
    },
    onError: (e) => toast.error(e.message),
  });
  const addTableOperation = useMutation({
    mutationFn: () => {
      const inventory = availableTables.data || [];
      return post<PipelineOperation[]>(`/pipelines/${id}/tables`, {
        tables: selectedTables.map((tableId) => {
          const table = inventory.find(
            (candidate) => candidate.id === tableId,
          )!;
          return {
            schema_name: table.schema_name,
            table_name: table.table_name,
            initial_data_strategy: initialDataStrategy,
            destination_handling: destinationHandling,
          };
        }),
      });
    },
    onSuccess: () => {
      toast.success("Add table operation queued");
      setAddTables(false);
      setSelectedTables([]);
      client.invalidateQueries({ queryKey: ["pipeline", id] });
      client.invalidateQueries({ queryKey: ["pipeline-operations", id] });
    },
    onError: (error) => toast.error(error.message),
  });
  const tableOperation = useMutation({
    mutationFn: async (action: {
      type: "resync" | "stop" | "remove";
      table: PipelineTable;
    }) => {
      if (action.type === "resync") {
        return post<PipelineOperation>(
          `/pipeline-tables/${action.table.id}/resync`,
          {
            scope: "ENTIRE_TABLE",
          },
        );
      }
      if (action.type === "stop") {
        return post<PipelineOperation>(
          `/pipeline-tables/${action.table.id}/snapshot/stop`,
        );
      }
      return api<PipelineOperation>(
        `/pipelines/${id}/tables/${action.table.id}`,
        {
          method: "DELETE",
          body: JSON.stringify({
            destination_handling: deleteDestinationData
              ? "DELETE_TABLE"
              : "KEEP_DATA",
            confirm_destination_delete: deleteDestinationData
              ? confirmDestinationDelete
              : false,
          }),
        },
      );
    },
    onSuccess: (_, action) => {
      toast.success(
        action.type === "resync"
          ? "Table resync queued"
          : action.type === "stop"
            ? "Snapshot stop requested"
            : "Table removal queued",
      );
      setTableAction(null);
      setDeleteDestinationData(false);
      setConfirmDestinationDelete(false);
      client.invalidateQueries({ queryKey: ["pipeline", id] });
      client.invalidateQueries({ queryKey: ["pipeline-operations", id] });
    },
    onError: (error) => toast.error(error.message),
  });
  if (query.isPending) return <Loading />;
  if (query.isError) return <ErrorPanel error={query.error} />;
  const p = query.data;
  const runtime = status.data;
  const health = derivePipelineHealth(
    runtime?.actual_state || p.actual_state,
    p.destinations,
    p.destinations.map((delivery) => delivery.destination.status),
    p.metrics.cdc_lag,
  );
  const tableCols: ColumnDef<PipelineTable>[] = [
    {
      accessorKey: "table_name",
      header: "Table",
      cell: ({ row }) =>
        `${row.original.schema_name}.${row.original.table_name}`,
    },
    {
      accessorKey: "cdc_status",
      header: "CDC",
      cell: ({ row }) => <Status value={row.original.cdc_status} />,
    },
    {
      accessorKey: "snapshot_status",
      header: "Snapshot",
      cell: ({ row }) => <Status value={row.original.snapshot_status} />,
    },
    {
      accessorKey: "schema_status",
      header: "Schema",
      cell: ({ row }) => <Status value={row.original.schema_status} />,
    },
    {
      accessorKey: "destination_status",
      header: "Destination",
      cell: ({ row }) => <Status value={row.original.destination_status} />,
    },
    {
      id: "lag",
      header: "Lag",
      cell: () => <span className="muted">Unavailable</span>,
    },
    {
      id: "last_event",
      header: "Last event",
      cell: () => <span className="muted">Unavailable</span>,
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <div className="table-actions">
          <Button
            variant="outline"
            disabled={row.original.snapshot_status === "RUNNING"}
            onClick={() =>
              setTableAction({ type: "resync", table: row.original })
            }
          >
            Resync
          </Button>
          {row.original.snapshot_status === "RUNNING" && (
            <Button
              variant="outline"
              onClick={() =>
                setTableAction({ type: "stop", table: row.original })
              }
            >
              Stop snapshot
            </Button>
          )}
          <Button
            variant="ghost"
            onClick={() =>
              setTableAction({ type: "remove", table: row.original })
            }
          >
            Remove
          </Button>
        </div>
      ),
    },
  ];
  return (
    <>
      <Link className="back-link" href="/pipelines">
        <ArrowLeft size={14} />
        All pipelines
      </Link>
      <PageHeader
        title={p.name}
        description={`${p.source.name} → ${p.destinations.map((delivery) => delivery.destination.name).join(", ") || "No destination"} · ${p.tables.length} tables`}
        eyebrow="DATA FLOW / PIPELINE"
      >
        <span title={health.reason}>
          <Status value={health.status} />
        </span>
        {!p.connector_id ? (
          <Button
            onClick={() => operation.mutate("deploy")}
            disabled={operation.isPending}
          >
            <Play size={16} />
            Deploy
          </Button>
        ) : (
          <>
            <Button
              variant="outline"
              disabled={operation.isPending}
              onClick={() =>
                operation.mutate(
                  p.desired_state === "PAUSED" ? "resume" : "pause",
                )
              }
            >
              {p.desired_state === "PAUSED" ? (
                <Play size={15} />
              ) : (
                <Pause size={15} />
              )}{" "}
              {p.desired_state === "PAUSED" ? "Resume" : "Pause"}
            </Button>
            <Button
              variant="outline"
              disabled={operation.isPending}
              onClick={() => operation.mutate("restart")}
            >
              <RefreshCw size={15} />
              Restart
            </Button>
          </>
        )}
        <Button
          variant="ghost"
          aria-label="Delete pipeline"
          onClick={() => setConfirm(true)}
        >
          <Trash2 size={16} />
        </Button>
      </PageHeader>
      {operation.isError && <ErrorPanel error={operation.error} />}{" "}
      {status.isError && <ErrorPanel error={status.error} />}{" "}
      {runtime?.error && (
        <ErrorPanel error={new Error(runtime.error.message)} />
      )}
      <Tabs
        tabs={[
          "Overview",
          "Deliveries",
          "Tables",
          "Topics",
          "Snapshots",
          "Events",
          "Schema",
          "Operations",
          "Connector",
          "Configuration",
          "Logs",
          "Activity",
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "Overview" && (
        <>
          <DataFlow
            pipeline={p}
            state={runtime?.actual_state || p.actual_state}
          />
          <div className="resource-summary">
            <MetricCard
              label="Captured tables"
              value={p.tables.length}
              detail="Selected source tables"
              icon={GitBranch}
            />
            <MetricCard
              label="Capture topics"
              value={new Set(p.tables.map((t) => t.topic_name)).size}
              detail="Configured topic mappings"
              icon={GitBranch}
            />
            <MetricCard
              label="Destinations"
              value={new Set(p.destinations.map((d) => d.destination_id)).size}
              detail={`${p.destinations.length} deliveries`}
              icon={GitBranch}
            />
          </div>
          <div className="infrastructure-strip">
            <span>
              Last event:{" "}
              <span
                className="muted"
                title="Event freshness requires a metrics provider"
              >
                Unavailable
              </span>
            </span>
          </div>
          <div className="metric-row">
            <div className="metric-card">
              <span>Actual runtime state</span>
              <strong className="metric-state">
                <Status value={runtime?.actual_state || p.actual_state} />
              </strong>
              <small>Observed from Kafka Connect</small>
            </div>
            <div className="metric-card">
              <span>Desired state</span>
              <strong className="metric-state">
                <Status value={p.desired_state} />
              </strong>
              <small>Managed by ClueCDC</small>
            </div>
            <div className="metric-card">
              <span>Events / second</span>
              <strong className="unavailable">Unavailable</strong>
              <small>Metrics provider required</small>
            </div>
            <div className="metric-card">
              <span>CDC lag</span>
              <strong className="unavailable">Unavailable</strong>
              <small>No reliable lag measurement</small>
            </div>
          </div>
          <div className="detail-grid">
            <section className="panel">
              <h2>Pipeline configuration</h2>
              <dl className="facts">
                <dt>Source</dt>
                <dd>
                  <Link href={`/sources/${p.source_id}`} className="text-link">
                    {p.source.name}
                  </Link>
                </dd>
                <dt>Kafka</dt>
                <dd>{p.kafka_cluster.name}</dd>
                <dt>Kafka Connect</dt>
                <dd>{p.connect_cluster.name}</dd>
                <dt>Connector</dt>
                <dd>
                  <code>{p.connector?.name || "Not deployed"}</code>
                </dd>
                <dt>Topic prefix</dt>
                <dd>
                  <code>{p.topic_prefix}</code>
                </dd>
                <dt>Tables / topics</dt>
                <dd>{p.tables.length}</dd>
                <dt>Snapshot mode</dt>
                <dd>{p.snapshot_mode}</dd>
              </dl>
            </section>
            <section className="panel">
              <h2>Capture runtime</h2>
              <p className="muted">
                CDC rows flow directly into Kafka. The API reads bounded samples
                for inspection.
              </p>
              <h3>Runtime tasks</h3>
              {runtime?.tasks.length ? (
                <div className="task-list">
                  {runtime.tasks.map((t) => (
                    <div key={t.id}>
                      <span>Task {t.id}</span>
                      <Status value={t.state} />
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">No running tasks have been observed.</p>
              )}
            </section>
          </div>
        </>
      )}
      {tab === "Deliveries" && (
        <section className="section">
          <div className="section-heading">
            <h2>Deliveries</h2>
            <Button asChild>
              <Link href={`/deliveries/new?pipeline_id=${id}`}>
                <Plus size={14} />
                Add delivery
              </Link>
            </Button>
          </div>
          {p.destinations?.length ? (
            <DataTable
              data={p.destinations}
              columns={[
                {
                  accessorKey: "name",
                  header: "Delivery",
                  cell: ({ row }) => (
                    <Link
                      className="text-link"
                      href={`/deliveries/${row.original.id}`}
                    >
                      {row.original.name}
                    </Link>
                  ),
                },
                {
                  id: "destination",
                  header: "Destination",
                  accessorFn: (delivery) => delivery.destination.name,
                },
                {
                  id: "topics",
                  header: "Topics",
                  cell: ({ row }) => row.original.topic_mapping_json.length,
                },
                { accessorKey: "delivery_mode", header: "Write mode" },
                {
                  accessorKey: "actual_state",
                  header: "Delivery state",
                  cell: ({ row }) => (
                    <Status value={row.original.actual_state} />
                  ),
                },
                {
                  accessorKey: "desired_state",
                  header: "Desired state",
                  cell: ({ row }) => (
                    <Status value={row.original.desired_state} />
                  ),
                },
              ]}
              getRowHref={(delivery) => `/deliveries/${delivery.id}`}
            />
          ) : (
            <Empty
              title="No deliveries configured"
              description="A delivery moves these Kafka topics to a destination."
              href={`/deliveries/new?pipeline_id=${id}`}
              action="Add delivery"
            />
          )}
        </section>
      )}
      {tab === "Tables" && (
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>Pipeline tables</h2>
              <p>Table-level capture, snapshot, schema, and delivery health.</p>
            </div>
            <Button
              onClick={() => setAddTables(true)}
              disabled={!p.connector_id}
            >
              <Plus size={15} />
              Add tables
            </Button>
          </div>
          <DataTable data={p.tables} columns={tableCols} />
        </section>
      )}{" "}
      {tab === "Topics" && (
        <DataTable
          data={p.tables}
          columns={[
            {
              accessorKey: "table_name",
              header: "Table",
              cell: ({ row }) =>
                `${row.original.schema_name}.${row.original.table_name}`,
            },
            {
              accessorKey: "topic_name",
              header: "Kafka topic",
              cell: ({ row }) => (
                <Link
                  href={`/kafka/topics/${encodeURIComponent(row.original.topic_name)}?cluster=${p.kafka_cluster_id}`}
                  className="text-link"
                >
                  {row.original.topic_name}
                </Link>
              ),
            },
          ]}
        />
      )}{" "}
      {tab === "Snapshots" && (
        <section className="panel">
          <h2>Snapshot control center</h2>
          <p className="muted">
            {p.metrics.notice}. Exact progress is shown only when Debezium emits
            it.
          </p>
          <dl className="facts">
            <dt>Mode</dt>
            <dd>{p.snapshot_mode}</dd>
            <dt>Tables total</dt>
            <dd>{p.tables.length}</dd>
            <dt>Tables completed</dt>
            <dd>
              {
                p.tables.filter((table) =>
                  ["COMPLETED", "NOT_REQUIRED"].includes(table.snapshot_status),
                ).length
              }
            </dd>
            <dt>Processed rows</dt>
            <dd>
              {p.tables.some((table) => table.snapshot_rows_processed !== null)
                ? number(
                    p.tables.reduce(
                      (total, table) =>
                        total + (table.snapshot_rows_processed || 0),
                      0,
                    ),
                  )
                : "Unavailable"}
            </dd>
            <dt>Started / completed</dt>
            <dd>Per-table below</dd>
          </dl>
          <p className="info-strip">
            READ events in the event explorer identify sampled snapshot records;
            they do not establish full progress.
          </p>
          <DataTable data={p.tables} columns={tableCols} />
        </section>
      )}{" "}
      {tab === "Events" && (
        <EventsExplorer
          initialCluster={p.kafka_cluster_id}
          initialTopic={p.tables[0]?.topic_name}
          allowedTopics={p.tables.map((t) => t.topic_name)}
        />
      )}{" "}
      {tab === "Schema" &&
        (schemas.isPending ? (
          <Loading />
        ) : schemas.isError ? (
          <ErrorPanel error={schemas.error} />
        ) : (
          <DataTable
            data={schemas.data.filter((s) =>
              p.tables.some(
                (t) =>
                  t.schema_name === s.schema_name &&
                  t.table_name === s.table_name,
              ),
            )}
            columns={[
              {
                accessorKey: "table_name",
                header: "Table",
                cell: ({ row }) =>
                  `${row.original.schema_name}.${row.original.table_name}`,
              },
              { accessorKey: "version", header: "Version" },
              {
                accessorKey: "diff_json",
                header: "Changes",
                cell: ({ row }) => (
                  <details>
                    <summary>{row.original.diff_json.length} changes</summary>
                    <JsonView value={row.original.diff_json} />
                  </details>
                ),
              },
              {
                accessorKey: "created_at",
                header: "Discovered",
                cell: ({ row }) => date(row.original.created_at),
              },
            ]}
          />
        ))}{" "}
      {tab === "Operations" &&
        (operations.isPending ? (
          <Loading />
        ) : operations.isError ? (
          <ErrorPanel error={operations.error} />
        ) : operations.data.length ? (
          <DataTable
            data={operations.data}
            columns={[
              { accessorKey: "type", header: "Operation" },
              {
                accessorKey: "status",
                header: "Status",
                cell: ({ row }) => <Status value={row.original.status} />,
              },
              {
                accessorKey: "current_step",
                header: "Current step",
                cell: ({ row }) => row.original.current_step || "Pending",
              },
              {
                accessorKey: "progress",
                header: "Progress",
                cell: ({ row }) =>
                  row.original.progress === null
                    ? "Unavailable"
                    : `${row.original.progress}%`,
              },
              {
                accessorKey: "created_at",
                header: "Created",
                cell: ({ row }) => date(row.original.created_at),
              },
              {
                id: "error",
                header: "Result",
                cell: ({ row }) =>
                  row.original.error_message ||
                  (row.original.status === "SUCCEEDED" ? "Completed" : "—"),
              },
              {
                id: "actions",
                header: "Actions",
                cell: ({ row }) =>
                  row.original.status === "FAILED" ? (
                    <Button
                      variant="outline"
                      disabled={retryOperation.isPending}
                      onClick={() => retryOperation.mutate(row.original.id)}
                    >
                      Retry
                    </Button>
                  ) : null,
              },
            ]}
          />
        ) : (
          <Empty
            title="No lifecycle operations"
            description="Add, remove, and resync actions will appear here and survive browser refreshes."
          />
        ))}{" "}
      {tab === "Connector" && (
        <section className="panel">
          <div className="section-heading">
            <h2>Connector / task status</h2>
            <Button variant="outline" onClick={() => status.refetch()}>
              Refresh runtime
            </Button>
          </div>
          <JsonView value={runtime || p.connector?.runtime_json} />
          {runtime?.tasks.map((t) => (
            <div key={t.id} className="task-action">
              <strong>Task {t.id}</strong>
              <Status value={t.state} />
              <span className="muted">{t.worker_id}</span>
              <Button
                variant="outline"
                disabled={operation.isPending}
                onClick={() => operation.mutate(`restart-task?task=${t.id}`)}
              >
                Restart task
              </Button>
            </div>
          ))}
        </section>
      )}{" "}
      {tab === "Configuration" && (
        <section className="panel">
          <h2>Generated connector configuration</h2>
          <p className="muted">
            Source credentials are resolved server-side and redacted from this
            view.
          </p>
          <JsonView value={p.config} />
        </section>
      )}{" "}
      {tab === "Logs" && (
        <section className="panel">
          <h2>Worker logs</h2>
          <p>
            Kafka Connect logs stay with the data plane. Run this command
            locally:
          </p>
          <pre className="json-view">
            docker compose logs --tail 100 kafka-connect
          </pre>
          <p className="muted">
            Task failures and runtime state are visible in the Connector tab and
            Error Center. Raw worker traces are withheld because they may
            contain credentials.
          </p>
        </section>
      )}{" "}
      {tab === "Activity" &&
        (activity.isPending ? (
          <Loading />
        ) : activity.isError ? (
          <ErrorPanel error={activity.error} />
        ) : (
          <DataTable
            data={activity.data}
            columns={[
              {
                accessorKey: "created_at",
                header: "Timestamp",
                cell: ({ row }) => date(row.original.created_at),
              },
              { accessorKey: "actor", header: "Actor" },
              { accessorKey: "action", header: "Action" },
            ]}
          />
        ))}
      <Dialog
        open={addTables}
        onOpenChange={setAddTables}
        title="Add tables"
        description="Extend this pipeline without replacing its connector, replication slot, publication, or offsets."
      >
        <div className="form-grid">
          <label className="full-span">
            Search source tables
            <input
              value={tableSearch}
              onChange={(event) => setTableSearch(event.target.value)}
              placeholder="schema or table"
            />
          </label>
        </div>
        {availableTables.isPending ? (
          <Loading />
        ) : availableTables.isError ? (
          <ErrorPanel error={availableTables.error} />
        ) : (
          <div className="selection-list lifecycle-table-picker">
            {availableTables.data
              .filter(
                (candidate) =>
                  !p.tables.some(
                    (current) =>
                      current.schema_name === candidate.schema_name &&
                      current.table_name === candidate.table_name,
                  ),
              )
              .map((candidate) => (
                <label
                  key={candidate.id}
                  className={`selection-card ${selectedTables.includes(candidate.id) ? "selected" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={selectedTables.includes(candidate.id)}
                    onChange={(event) =>
                      setSelectedTables((current) =>
                        event.target.checked
                          ? [...current, candidate.id]
                          : current.filter((id) => id !== candidate.id),
                      )
                    }
                  />
                  <span>
                    <strong>
                      {candidate.schema_name}.{candidate.table_name}
                    </strong>
                    <small>
                      {candidate.estimated_rows === null
                        ? "Rows unavailable"
                        : `${number(candidate.estimated_rows)} estimated rows`}
                      {" · "}
                      PK: {candidate.primary_key_columns.join(", ") || "None"}
                    </small>
                  </span>
                  <Status value={candidate.cdc_status} />
                </label>
              ))}
          </div>
        )}
        <h3>Existing data</h3>
        <div className="selection-list compact-options">
          <label
            className={`selection-card ${initialDataStrategy === "BACKFILL" ? "selected" : ""}`}
          >
            <input
              type="radio"
              name="initial-data"
              checked={initialDataStrategy === "BACKFILL"}
              onChange={() => setInitialDataStrategy("BACKFILL")}
            />
            <span>
              <strong>Backfill existing rows</strong>
              <small>
                Run an incremental snapshot for only the selected tables.
              </small>
            </span>
          </label>
          <label
            className={`selection-card ${initialDataStrategy === "FUTURE_ONLY" ? "selected" : ""}`}
          >
            <input
              type="radio"
              name="initial-data"
              checked={initialDataStrategy === "FUTURE_ONLY"}
              onChange={() => setInitialDataStrategy("FUTURE_ONLY")}
            />
            <span>
              <strong>Capture future changes only</strong>
              <small>Do not read rows that already exist in the source.</small>
            </span>
          </label>
        </div>
        {!!p.destinations.length && (
          <label>
            Destination handling
            <select
              value={destinationHandling}
              onChange={(event) =>
                setDestinationHandling(
                  event.target.value as typeof destinationHandling,
                )
              }
            >
              <option value="AUTO_CREATE">Create automatically</option>
              <option value="USE_EXISTING">Use existing table</option>
              <option value="VALIDATE_ONLY">Validate only</option>
            </select>
          </label>
        )}
        {selectedTables.some((id) => {
          const table = availableTables.data?.find(
            (candidate) => candidate.id === id,
          );
          return table && !table.primary_key_columns.length;
        }) && (
          <p className="warning-strip">
            No primary key detected. Reliable key-based upserts and deletes may
            not be possible. Incremental backfill requires a primary key.
          </p>
        )}
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setAddTables(false)}>
            Cancel
          </Button>
          <Button
            disabled={!selectedTables.length || addTableOperation.isPending}
            onClick={() => addTableOperation.mutate()}
          >
            Add {selectedTables.length || ""} table
            {selectedTables.length === 1 ? "" : "s"}
          </Button>
        </div>
        {addTableOperation.isError && (
          <ErrorPanel error={addTableOperation.error} />
        )}
      </Dialog>
      <Dialog
        open={tableAction?.type === "stop"}
        onOpenChange={(open) => {
          if (!open) setTableAction(null);
        }}
        title="Stop snapshot?"
        description="CDC streaming remains active, but the current table backfill will be cancelled."
      >
        <p>
          Stop the incremental snapshot for{" "}
          <code>
            {tableAction?.table.schema_name}.{tableAction?.table.table_name}
          </code>
          ?
        </p>
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setTableAction(null)}>
            Continue snapshot
          </Button>
          <Button
            disabled={tableOperation.isPending}
            onClick={() => tableAction && tableOperation.mutate(tableAction)}
          >
            Stop snapshot
          </Button>
        </div>
      </Dialog>
      <Dialog
        open={tableAction?.type === "resync"}
        onOpenChange={(open) => {
          if (!open) setTableAction(null);
        }}
        title="Resync table?"
        description="Streaming CDC will continue while ClueCDC re-reads the table."
      >
        <p>
          An incremental snapshot will re-read{" "}
          <code>
            {tableAction?.table.schema_name}.{tableAction?.table.table_name}
          </code>
          . No connector, slot, offset, or topic will be recreated.
        </p>
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setTableAction(null)}>
            Cancel
          </Button>
          <Button
            disabled={tableOperation.isPending}
            onClick={() => tableAction && tableOperation.mutate(tableAction)}
          >
            Resync table
          </Button>
        </div>
      </Dialog>
      <Dialog
        open={tableAction?.type === "remove"}
        onOpenChange={(open) => {
          if (!open) setTableAction(null);
        }}
        title="Remove table from CDC?"
        description="The Kafka topic is retained. Other pipeline tables continue streaming."
      >
        <p>
          Remove{" "}
          <code>
            {tableAction?.table.schema_name}.{tableAction?.table.table_name}
          </code>{" "}
          from the connector filter, PostgreSQL publication, and sink
          subscriptions.
        </p>
        <div className="selection-list compact-options">
          <label
            className={`selection-card ${!deleteDestinationData ? "selected" : ""}`}
          >
            <input
              type="radio"
              name="destination-removal"
              checked={!deleteDestinationData}
              onChange={() => setDeleteDestinationData(false)}
            />
            <span>
              <strong>Keep destination data</strong>
              <small>Recommended and selected by default.</small>
            </span>
          </label>
          <label
            className={`selection-card ${deleteDestinationData ? "selected" : ""}`}
          >
            <input
              type="radio"
              name="destination-removal"
              checked={deleteDestinationData}
              onChange={() => setDeleteDestinationData(true)}
            />
            <span>
              <strong>Delete destination table</strong>
              <small>
                This permanently deletes the replicated table and its data.
              </small>
            </span>
          </label>
        </div>
        {deleteDestinationData && (
          <label className="danger-confirmation">
            <input
              type="checkbox"
              checked={confirmDestinationDelete}
              onChange={(event) =>
                setConfirmDestinationDelete(event.target.checked)
              }
            />
            I understand that destination data will be permanently deleted.
          </label>
        )}
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setTableAction(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={
              tableOperation.isPending ||
              (deleteDestinationData && !confirmDestinationDelete)
            }
            onClick={() => tableAction && tableOperation.mutate(tableAction)}
          >
            Remove table
          </Button>
        </div>
        {tableOperation.isError && <ErrorPanel error={tableOperation.error} />}
      </Dialog>
      <Dialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Delete this pipeline?"
        description="The connector and pipeline metadata will be removed. Kafka topics and PostgreSQL replication slots remain; an administrator can clean them up after confirming they are no longer needed."
      >
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setConfirm(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={operation.isPending}
            onClick={() => operation.mutate("delete")}
          >
            Delete pipeline
          </Button>
        </div>
        {operation.isError && <ErrorPanel error={operation.error} />}
      </Dialog>
    </>
  );
}
