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
import { DetailDrawer, MetricCard } from "@/components/operational";
import { api, date, number, post } from "@/lib/api";
import type {
  Audit,
  Job,
  Pipeline,
  Readiness,
  Source,
  SourceTable,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Database,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { SourceEditor } from "./source-editor";
import { useAuthorization } from "@/lib/auth";

export function SourceDetailPage({ id }: { id: string }) {
  const { can } = useAuthorization();
  const canWrite = can("sources.write");
  const canDelete = can("sources.admin");
  const canAudit = can("audit.read");
  const router = useRouter();
  const client = useQueryClient();
  const [tab, setTab] = useState("Overview");
  const [edit, setEdit] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [selected, setSelected] = useState<SourceTable | null>(null);
  const [schema, setSchema] = useState("");
  const [ready, setReady] = useState(false);
  const [pk, setPk] = useState(false);
  const [size, setSize] = useState("");
  const source = useQuery({
    queryKey: ["source", id],
    queryFn: () => api<Source>(`/sources/${id}`),
  });
  const tables = useQuery({
    queryKey: ["source-tables", id, schema, ready, pk, size],
    queryFn: () =>
      api<SourceTable[]>(
        `/sources/${id}/tables?${new URLSearchParams({ ...(schema ? { schema } : {}), ...(ready ? { cdc_ready: "true" } : {}), ...(pk ? { has_primary_key: "true" } : {}), ...(size ? { min_size: size } : {}) })}`,
      ),
  });
  const sourcePipelines = useQuery({
    queryKey: ["pipelines", "source", id],
    queryFn: () => api<Pipeline[]>(`/pipelines?source_id=${id}`),
  });
  const readiness = useQuery({
    queryKey: ["readiness", id],
    queryFn: () => api<Readiness>(`/sources/${id}/cdc-readiness`),
    enabled: tab === "CDC Readiness",
  });
  const activity = useQuery({
    queryKey: ["audit", id],
    queryFn: () => api<Audit[]>(`/audit?resource_id=${id}`),
    enabled: canAudit && tab === "Activity",
  });
  const test = useMutation({
    mutationFn: () => post<{ status: string }>(`/sources/${id}/test`),
    onSuccess: () => {
      toast.success("Database CDC readiness verified");
      client.invalidateQueries({ queryKey: ["source"] });
    },
    onError: (e) => {
      toast.error(e.message);
      client.invalidateQueries({ queryKey: ["source"] });
    },
  });
  const discovery = useMutation({
    mutationFn: async () => {
      const job = await post<Job>(`/sources/${id}/discover`);
      for (let i = 0; i < 180; i++) {
        const current = await api<Job>(`/jobs/${job.id}`);
        if (current.status === "COMPLETED") return current;
        if (current.status === "FAILED")
          throw new Error(current.error || "Discovery failed");
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      throw new Error(
        "Discovery continues in the worker. Refresh tables to inspect the result.",
      );
    },
    onSuccess: () => {
      toast.success("Table discovery completed");
      client.invalidateQueries({ queryKey: ["source-tables"] });
      client.invalidateQueries({ queryKey: ["source"] });
      client.invalidateQueries({ queryKey: ["sources"] });
      client.invalidateQueries({ queryKey: ["schemas"] });
      setTab("Tables");
    },
    onError: (e) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: () => api(`/sources/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Source deleted");
      router.push("/sources");
    },
    onError: (e) => toast.error(e.message),
  });
  if (source.isPending) return <Loading />;
  if (source.isError) return <ErrorPanel error={source.error} />;
  const s = source.data;
  const columns: ColumnDef<SourceTable>[] = [
    {
      accessorKey: "table_name",
      header: "Table",
      cell: ({ row }) => (
        <button className="text-link" onClick={() => setSelected(row.original)}>
          {row.original.schema_name}.{row.original.table_name}
        </button>
      ),
    },
    {
      accessorKey: "primary_key_columns",
      header: "Primary key",
      cell: ({ row }) => (
        <code>{row.original.primary_key_columns.join(", ") || "None"}</code>
      ),
    },
    {
      accessorKey: "estimated_rows",
      header: "Est. rows",
      cell: ({ row }) => number(row.original.estimated_rows),
    },
    {
      accessorKey: "estimated_size_bytes",
      header: "Est. size",
      cell: ({ row }) => `${number(row.original.estimated_size_bytes)} B`,
    },
    {
      accessorKey: "cdc_status",
      header: "Readiness",
      cell: ({ row }) => <Status value={row.original.cdc_status} />,
    },
  ];
  return (
    <>
      <Link className="back-link" href="/sources">
        <ArrowLeft size={14} />
        All sources
      </Link>
      <PageHeader
        title={s.name}
        description={`${s.host}:${s.port} / ${s.database_name}`}
        eyebrow="SOURCE / POSTGRESQL"
      >
        <Status value={s.status} />
        {canWrite && (
          <>
            <Button
              variant="outline"
              disabled={test.isPending}
              onClick={() => test.mutate()}
            >
              <CheckCircle2 size={16} />
              Test connection
            </Button>
            <Button
              disabled={discovery.isPending}
              onClick={() => discovery.mutate()}
            >
              {discovery.isPending ? (
                <Loader2 className="spin" size={16} />
              ) : (
                <RefreshCw size={16} />
              )}
              Discover tables
            </Button>
          </>
        )}
      </PageHeader>
      {test.isError && <ErrorPanel error={test.error} />}{" "}
      {discovery.isError && <ErrorPanel error={discovery.error} />}
      <div className="resource-summary">
        <MetricCard
          label="Discovered tables"
          value={s.tables ?? "Unavailable"}
          detail="Last saved inventory"
          icon={Database}
        />
        <MetricCard
          label="CDC-ready tables"
          value={s.cdc_ready_tables ?? "Unavailable"}
          detail="Discovered readiness checks"
          icon={CheckCircle2}
        />
        <MetricCard
          label="Capture pipelines"
          value={
            sourcePipelines.isPending
              ? "Loading"
              : sourcePipelines.isError
                ? "Unavailable"
                : sourcePipelines.data.filter((p) => p.source_id === id).length
          }
          detail="Associated capture resources"
          icon={Database}
        />
        <MetricCard
          label="Latest discovery"
          value={
            <span className="summary-date">{date(s.last_discovery_at)}</span>
          }
          detail="Latest completed table discovery"
          icon={RefreshCw}
        />
      </div>
      <Tabs
        tabs={[
          "Overview",
          "Tables",
          "CDC Readiness",
          "Pipelines",
          "Configuration",
          ...(canAudit ? ["Activity"] : []),
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "Pipelines" &&
        (sourcePipelines.isPending ? (
          <Loading />
        ) : sourcePipelines.isError ? (
          <ErrorPanel error={sourcePipelines.error} />
        ) : (
          <DataTable
            data={sourcePipelines.data.filter((p) => p.source_id === id)}
            getRowHref={(p) => `/pipelines/${p.id}`}
            columns={[
              { accessorKey: "name", header: "Pipeline" },
              {
                accessorKey: "actual_state",
                header: "Capture state",
                cell: ({ row }) => <Status value={row.original.actual_state} />,
              },
              { accessorKey: "tables", header: "Topics" },
              { accessorKey: "destinations", header: "Destinations" },
            ]}
          />
        ))}
      {tab === "Overview" && (
        <div className="detail-grid">
          <section className="panel">
            <h2>Connection</h2>
            <dl className="facts">
              <dt>Type</dt>
              <dd>{s.type === "mysql" ? "MySQL" : "PostgreSQL"}</dd>
              <dt>Environment</dt>
              <dd>{s.environment}</dd>
              <dt>Database</dt>
              <dd>{s.database_name}</dd>
              <dt>Username</dt>
              <dd>{s.username}</dd>
              <dt>TLS</dt>
              <dd>{s.ssl_enabled ? "Verified" : "Disabled"}</dd>
              <dt>Last checked</dt>
              <dd>{date(s.last_health_check_at)}</dd>
            </dl>
          </section>
          <section className="panel">
            <h2>Start capturing changes</h2>
            <p className="muted">
              Test the connection, inspect readiness, and discover tables before
              creating a pipeline.
            </p>
            <div className="source-steps">
              <span>01 · Test connection</span>
              <span>02 · Assess CDC readiness</span>
              <span>03 · Discover & select tables</span>
            </div>
            {can("pipelines.write") && (
              <Button asChild>
                <Link href={`/pipelines/new?source=${id}`}>
                  Create pipeline
                  <ArrowRight size={15} />
                </Link>
              </Button>
            )}
          </section>
        </div>
      )}
      {tab === "Tables" && (
        <>
          <div className="filters">
            <Input
              aria-label="Schema filter"
              placeholder="Schema name"
              value={schema}
              onChange={(e) => setSchema(e.target.value)}
            />
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={ready}
                onChange={(e) => setReady(e.target.checked)}
              />
              CDC ready
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={pk}
                onChange={(e) => setPk(e.target.checked)}
              />
              Has primary key
            </label>
            <Input
              type="number"
              aria-label="Minimum size in bytes"
              placeholder="Min size (bytes)"
              value={size}
              onChange={(e) => setSize(e.target.value)}
            />
          </div>
          {tables.isPending ? (
            <Loading />
          ) : tables.isError ? (
            <ErrorPanel error={tables.error} />
          ) : tables.data.length ? (
            <DataTable data={tables.data} columns={columns} />
          ) : (
            <Empty
              title="No tables discovered"
              description="Run discovery to inspect schemas, columns, primary keys, and indexes."
            />
          )}
        </>
      )}
      {tab === "CDC Readiness" &&
        (readiness.isPending ? (
          <Loading />
        ) : readiness.isError ? (
          <ErrorPanel
            error={readiness.error}
            retry={() => readiness.refetch()}
          />
        ) : (
          <section className="panel">
            <div className="section-heading">
              <h2>CDC readiness assessment</h2>
              <Status value={readiness.data.status} />
              <Button variant="outline" onClick={() => readiness.refetch()}>
                Recheck
              </Button>
            </div>
            <p className="muted">
              Read-only checks. Configuration changes require a database
              administrator.
            </p>
            <div className="readiness-list">
              {readiness.data.checks.map((check) => (
                <div key={check.key}>
                  <Status value={check.status} />
                  <div>
                    <strong>{check.name}</strong>
                    <p>{check.description}</p>
                    <small>
                      Current: {JSON.stringify(check.current_value)} · Expected:{" "}
                      {JSON.stringify(check.expected_value)}
                    </small>
                    {check.recommended_action && (
                      <p className="remediation">{check.recommended_action}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        ))}
      {tab === "Configuration" && (
        <section className="panel">
          <h2>Source configuration</h2>
          <JsonView value={s} />
          {(canWrite || canDelete) && (
            <div className="toolbar">
              {canWrite && (
                <Button variant="outline" onClick={() => setEdit(true)}>
                  Edit source
                </Button>
              )}
              {canDelete && (
                <Button variant="destructive" onClick={() => setConfirm(true)}>
                  <Trash2 size={15} />
                  Delete source
                </Button>
              )}
            </div>
          )}
        </section>
      )}
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
      {canWrite && (
        <SourceEditor source={s} open={edit} onOpenChange={setEdit} />
      )}
      {canDelete && (
        <Dialog
          open={confirm}
          onOpenChange={setConfirm}
          title="Delete source?"
          description="The source connection, encrypted credential, and discovery metadata will be deleted. Associated pipelines must be deleted first."
        >
          <div className="dialog-actions">
            <Button variant="outline" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              Delete source
            </Button>
          </div>
          {remove.isError && <ErrorPanel error={remove.error} />}
        </Dialog>
      )}
      <DetailDrawer
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title={
          selected
            ? `${selected.schema_name}.${selected.table_name}`
            : "Table details"
        }
        description="Discovered table structure and capture readiness."
      >
        {selected && (
          <>
            <div className="toolbar">
              <Status value={selected.cdc_status} />
              <code>
                PK: {selected.primary_key_columns.join(", ") || "None"}
              </code>
            </div>
            {selected.cdc_issues.map((issue) => (
              <p className="remediation" key={issue}>
                {issue}
              </p>
            ))}
            <h3>Columns</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Nullable</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.columns_json.map((c) => (
                    <tr key={c.name}>
                      <td>{c.name}</td>
                      <td>
                        <code>{c.type}</code>
                      </td>
                      <td>{c.nullable ? "Yes" : "No"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3>Indexes</h3>
            <JsonView value={selected.indexes_json} />
          </>
        )}
      </DetailDrawer>
    </>
  );
}
