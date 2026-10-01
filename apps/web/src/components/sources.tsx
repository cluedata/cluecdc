"use client";
import { InventoryStrip } from "./operational";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Database,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import type {
  Audit,
  Pipeline,
  Job,
  Readiness,
  DatabaseProviderMetadata,
  Source,
  SourceTable,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { api, date, number, post } from "@/lib/api";
import { sourceSchema, SourceForm } from "@/lib/validation";
import {
  DataTable,
  Empty,
  ErrorPanel,
  Field,
  JsonView,
  Loading,
  PageHeader,
  Status,
  Tabs,
} from "./common";

import { DetailDrawer, FilterBar, MetricCard, Panel } from "./operational";

export function SourceEditor({
  source,
  open,
  onOpenChange,
  fullPage = false,
}: {
  fullPage?: boolean;
  source?: Source;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const client = useQueryClient();
  const providers = useQuery({
    queryKey: ["database-providers"],
    queryFn: () => api<DatabaseProviderMetadata[]>("/database-providers"),
  });
  const form = useForm<SourceForm>({
    resolver: zodResolver(sourceSchema),
    defaultValues: source
      ? { ...source, password: "" }
      : {
          name: "Commerce PostgreSQL",
          type: "postgresql",
          environment: "development",
          host: "cdc-source-postgres",
          port: 5432,
          database_name: "commerce",
          username: "cdc_user",
          password: "",
          ssl_enabled: false,
          provider_options: { connection_timeout_seconds: 10 },
        },
  });
  const selectedType = useWatch({ control: form.control, name: "type" });
  const save = useMutation({
    mutationFn: (values: SourceForm) =>
      api<Source>(source ? `/sources/${source.id}` : "/sources", {
        method: source ? "PUT" : "POST",
        body: JSON.stringify({
          ...values,
          password: values.password || undefined,
        }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["sources"] });
      client.invalidateQueries({ queryKey: ["source"] });
      client.invalidateQueries({ queryKey: ["source-tables"] });
      toast.success(source ? "Source updated" : "Source registered");
      onOpenChange(false);
    },
    onError: (e) => toast.error(e.message),
  });
  const errors = form.formState.errors;
  const content = (
    <form
      onSubmit={form.handleSubmit((v) => {
        if (!source && !v.password) {
          form.setError("password", { message: "Password is required" });
          return;
        }
        save.mutate(v);
      })}
      className="form-grid"
    >
      <Field label="Source name" error={errors.name?.message}>
        <Input {...form.register("name")} />
      </Field>
      <Field label="Environment" error={errors.environment?.message}>
        <select {...form.register("environment")}>
          <option>development</option>
          <option>staging</option>
          <option>production</option>
        </select>
      </Field>
      <Field
        label="Database type"
        hint="Choose the CDC engine explicitly; ports do not determine it."
      >
        <select
          {...form.register("type")}
          onChange={(event) => {
            const type = event.target.value as SourceForm["type"];
            form.setValue("type", type);
            const provider = providers.data?.find((item) => item.type === type);
            if (provider) form.setValue("port", provider.default_port);
          }}
        >
          {(providers.data || [])
            .filter((item) => item.source_supported)
            .map((provider) => (
              <option key={provider.type} value={provider.type}>
                {provider.display_name}
              </option>
            ))}
        </select>
      </Field>
      <Field label="Database" error={errors.database_name?.message}>
        <Input {...form.register("database_name")} />
      </Field>
      <Field
        label="Host"
        error={errors.host?.message}
        hint="Use the hostname reachable from the API and Connect workers."
      >
        <Input {...form.register("host")} />
      </Field>
      <Field label="Port" error={errors.port?.message}>
        <Input
          type="number"
          {...form.register("port", { valueAsNumber: true })}
        />
      </Field>
      <Field label="Username" error={errors.username?.message}>
        <Input autoComplete="off" {...form.register("username")} />
      </Field>
      <Field
        label={source ? "Password (blank keeps current)" : "Password"}
        error={errors.password?.message}
      >
        <Input
          type="password"
          autoComplete="new-password"
          {...form.register("password")}
        />
      </Field>
      <label className="checkbox-row full-width">
        <input type="checkbox" {...form.register("ssl_enabled")} />
        Verify TLS connection (server certificate must be trusted)
      </label>
      <details className="full-width">
        <summary>Advanced settings</summary>
        <div className="form-grid">
          {selectedType === "mysql" && (
            <Field
              label="Server ID"
              hint="Leave blank to allocate and persist a unique ID."
            >
              <Input
                type="number"
                {...form.register("provider_options.server_id", {
                  setValueAs: (value) =>
                    value === "" ? undefined : Number(value),
                })}
              />
            </Field>
          )}
          <Field label="Connection timeout (seconds)">
            <Input
              type="number"
              {...form.register("provider_options.connection_timeout_seconds", {
                valueAsNumber: true,
              })}
            />
          </Field>
        </div>
      </details>
      {save.isError && (
        <div className="full-width">
          <ErrorPanel error={save.error} />
        </div>
      )}
      <div className="dialog-actions full-width">
        <Button
          type="button"
          variant="outline"
          onClick={() => onOpenChange(false)}
        >
          Cancel
        </Button>
        <Button disabled={save.isPending}>
          {save.isPending && <Loader2 size={15} className="spin" />}
          {source ? "Save changes" : "Register source"}
        </Button>
      </div>
    </form>
  );
  return fullPage ? (
    <div className="source-form-page">
      <Panel
        title="Connection details"
        description="Database credentials are encrypted in the control plane and are never returned."
      >
        {content}
      </Panel>
    </div>
  ) : (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={source ? "Edit source" : "Register a source"}
      description="Connect a supported database. Credentials are encrypted in the control plane."
    >
      {content}
    </Dialog>
  );
}
export function SourceCreatePage() {
  const router = useRouter();
  return (
    <>
      <Link className="back-link" href="/sources">
        <ArrowLeft size={14} />
        All sources
      </Link>
      <PageHeader
        title="Add source"
        description="Choose PostgreSQL or MySQL, validate CDC readiness, then discover tables."
        eyebrow="DATA MOVEMENT / SOURCE"
      />
      <SourceEditor
        fullPage
        open
        onOpenChange={() => router.push("/sources")}
      />
    </>
  );
}

export function SourcesPage() {
  const [environment, setEnvironment] = useState("");
  const [health, setHealth] = useState("");
  const [readinessFilter, setReadinessFilter] = useState("");
  const query = useQuery({
    queryKey: ["sources"],
    queryFn: () => api<Source[]>("/sources"),
  });
  const columns: ColumnDef<Source>[] = [
    {
      accessorKey: "name",
      header: "Source",
      cell: ({ row }) => (
        <Link className="entity-link" href={`/sources/${row.original.id}`}>
          <span className="table-icon">
            <Database size={16} />
          </span>
          <div>
            {row.original.name}
            <small>
              {row.original.host}:{row.original.port}
            </small>
          </div>
        </Link>
      ),
    },
    {
      accessorKey: "type",
      header: "Type",
      cell: ({ row }) =>
        row.original.type === "mysql" ? "MySQL" : "PostgreSQL",
    },
    {
      accessorKey: "environment",
      header: "Environment",
      cell: ({ row }) => (
        <span className="environment-tag">{row.original.environment}</span>
      ),
    },
    { accessorKey: "database_name", header: "Database" },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <Status value={row.original.status} />,
    },
    { accessorKey: "tables", header: "Tables" },
    { accessorKey: "cdc_ready_tables", header: "CDC ready" },
    {
      accessorKey: "last_health_check_at",
      header: "Last checked",
      cell: ({ row }) => (
        <span className="muted">{date(row.original.last_health_check_at)}</span>
      ),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <Link
          href={`/sources/${row.original.id}`}
          aria-label={`Open ${row.original.name}`}
        >
          <ArrowRight size={17} />
        </Link>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title="Sources"
        description="Register databases, explore tables, and assess capture readiness."
        eyebrow="SOURCE"
      >
        <Button asChild>
          <Link href="/sources/new">
            <Plus size={16} />
            Add source
          </Link>
        </Button>
      </PageHeader>
      {query.data && (
        <InventoryStrip
          items={[
            { label: "Registered sources", value: query.data.length },
            {
              label: "Healthy",
              value: query.data.filter((s) => s.status === "HEALTHY").length,
            },
            {
              label: "Discovered tables",
              value: query.data.reduce((n, s) => n + (s.tables || 0), 0),
            },
            {
              label: "CDC ready",
              value: query.data.reduce(
                (n, s) => n + (s.cdc_ready_tables || 0),
                0,
              ),
            },
          ]}
        />
      )}
      <div className="info-strip">
        <Database size={18} />
        <span>
          PostgreSQL logical replication and MySQL binary-log CDC are available.
        </span>
      </div>
      <FilterBar>
        <select
          aria-label="Source environment"
          value={environment}
          onChange={(e) => setEnvironment(e.target.value)}
        >
          <option value="">All environments</option>
          {Array.from(new Set(query.data?.map((s) => s.environment))).map(
            (v) => (
              <option key={v}>{v}</option>
            ),
          )}
        </select>
        <select
          aria-label="Source health"
          value={health}
          onChange={(e) => setHealth(e.target.value)}
        >
          <option value="">All health states</option>
          {["HEALTHY", "UNHEALTHY", "UNKNOWN"].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <select
          aria-label="Source CDC readiness"
          value={readinessFilter}
          onChange={(e) => setReadinessFilter(e.target.value)}
        >
          <option value="">All readiness states</option>
          <option value="ready">Has CDC-ready tables</option>
          <option value="review">Requires discovery or review</option>
        </select>
      </FilterBar>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : query.data.length ? (
        <DataTable
          getRowHref={(s) => `/sources/${s.id}`}
          data={query.data.filter(
            (s) =>
              (!environment || s.environment === environment) &&
              (!health || s.status === health) &&
              (!readinessFilter ||
                (readinessFilter === "ready"
                  ? (s.cdc_ready_tables || 0) > 0
                  : !(s.cdc_ready_tables || 0))),
          )}
          columns={columns}
        />
      ) : (
        <Empty
          title="Connect your first database"
          description="Add a database connection, test it, then discover tables to assess capture readiness."
          href="/sources/new"
          action="Add source"
        />
      )}
    </>
  );
}

export function SourceDetailPage({ id }: { id: string }) {
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
    enabled: tab === "Activity",
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
          "Activity",
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
            <Button asChild>
              <Link href={`/pipelines/new?source=${id}`}>
                Create pipeline
                <ArrowRight size={15} />
              </Link>
            </Button>
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
          <div className="toolbar">
            <Button variant="outline" onClick={() => setEdit(true)}>
              Edit source
            </Button>
            <Button variant="destructive" onClick={() => setConfirm(true)}>
              <Trash2 size={15} />
              Delete source
            </Button>
          </div>
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
      <SourceEditor source={s} open={edit} onOpenChange={setEdit} />
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
