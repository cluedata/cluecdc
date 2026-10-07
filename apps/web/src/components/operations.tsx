"use client";
import { InventoryStrip } from "./operational";
import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type {
  Audit,
  OperationalError,
  SchemaVersion,
  Pipeline,
  Destination,
  Json,
} from "@cluecdc/contracts";
import { Button } from "@cluecdc/ui";
import { api, date } from "@/lib/api";
import {
  DataTable,
  Empty,
  ErrorPanel,
  JsonView,
  Loading,
  PageHeader,
  Status,
} from "./common";

import { DetailDrawer, FilterBar, Panel } from "./operational";
import { changeClassification } from "./overview";
export { OverviewPage } from "./overview";

function auditFields(
  before: Json,
  after: Json,
  prefix = "",
): { field: string; before: Json; after: Json }[] {
  const isRecord = (v: Json): v is Record<string, Json> =>
    v !== null && typeof v === "object" && !Array.isArray(v);
  if (isRecord(before) || isRecord(after)) {
    const a = isRecord(before) ? before : {};
    const b = isRecord(after) ? after : {};
    return Array.from(new Set([...Object.keys(a), ...Object.keys(b)])).flatMap(
      (k) =>
        auditFields(a[k] ?? null, b[k] ?? null, prefix ? `${prefix}.${k}` : k),
    );
  }
  return JSON.stringify(before) === JSON.stringify(after)
    ? []
    : [{ field: prefix || "Record", before, after }];
}
export function AuditPage() {
  const [selected, setSelected] = useState<Audit | null>(null);
  const query = useQuery({
    queryKey: ["audit", "all"],
    queryFn: () => api<Audit[]>("/audit?limit=500"),
  });
  return (
    <>
      <PageHeader
        title="Audit trail"
        description="Configuration mutations, lifecycle operations, and runtime observations."
        eyebrow="OPERATIONS"
      />
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} />
      ) : (
        <DataTable
          onRowClick={(a) => setSelected(a)}
          data={query.data}
          columns={[
            {
              accessorKey: "created_at",
              header: "Timestamp",
              cell: ({ row }) => date(row.original.created_at),
            },
            { accessorKey: "actor", header: "Actor" },
            {
              accessorKey: "action",
              header: "Action",
              cell: ({ row }) => (
                <button
                  className="text-link"
                  onClick={() => setSelected(row.original)}
                >
                  {row.original.action}
                </button>
              ),
            },
            { accessorKey: "resource_type", header: "Resource" },
            {
              accessorKey: "resource_id",
              header: "Resource ID",
              cell: ({ row }) => <code>{row.original.resource_id}</code>,
            },
          ]}
        />
      )}
      <DetailDrawer
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title={selected?.action || "Audit record"}
        description="Secrets are removed from configuration snapshots."
      >
        {selected && (
          <>
            <dl className="facts">
              <dt>Actor</dt>
              <dd>{selected.actor}</dd>
              <dt>Timestamp</dt>
              <dd>{date(selected.created_at)}</dd>
              <dt>Resource</dt>
              <dd>{selected.resource_type}</dd>
            </dl>
            <Panel title="Changed fields">
              <DataTable
                data={auditFields(selected.before_json, selected.after_json)}
                emptyTitle="No configuration fields changed"
                columns={[
                  { accessorKey: "field", header: "Field" },
                  {
                    accessorKey: "before",
                    header: "Before",
                    cell: ({ row }) => (
                      <code className="diff-value">
                        {JSON.stringify(row.original.before)}
                      </code>
                    ),
                  },
                  {
                    accessorKey: "after",
                    header: "After",
                    cell: ({ row }) => (
                      <code className="diff-value">
                        {JSON.stringify(row.original.after)}
                      </code>
                    ),
                  },
                ]}
              />
            </Panel>
            <details>
              <summary>Configuration snapshots</summary>
              <h3>Before</h3>
              <JsonView value={selected.before_json} />
              <h3>After</h3>
              <JsonView value={selected.after_json} />
            </details>
          </>
        )}
      </DetailDrawer>
    </>
  );
}

export function ErrorsPage() {
  const client = useQueryClient();
  const [filter, setFilter] = useState("OPEN");
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [destination, setDestination] = useState("");
  const [selected, setSelected] = useState<OperationalError | null>(null);
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
  });
  const destinations = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api<Destination[]>("/destinations"),
  });
  const change = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api(`/operations/errors/${id}?status=${status}`, { method: "PATCH" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["errors"] }),
    onError: (error) => toast.error(error.message),
  });
  const query = useQuery({
    queryKey: ["errors"],
    queryFn: () => api<OperationalError[]>("/operations/errors"),
    refetchInterval: 10000,
  });
  return (
    <>
      <PageHeader
        title="Error center"
        description="Capture, destination, and sink failures with linked runtime resources."
        eyebrow="OPERATIONS"
      />
      {query.data && (
        <InventoryStrip
          items={[
            { label: "Matching incidents", value: query.data.length },
            {
              label: "Open",
              value: query.data.filter((e) => e.status === "OPEN").length,
            },
            {
              label: "Acknowledged",
              value: query.data.filter((e) => e.status === "ACKNOWLEDGED")
                .length,
            },
            {
              label: "Resolved",
              value: query.data.filter((e) => e.status === "RESOLVED").length,
            },
          ]}
        />
      )}
      <FilterBar>
        <select
          aria-label="Filter incidents"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="">All incidents</option>
          <option value="OPEN">Open</option>
          <option value="ACKNOWLEDGED">Acknowledged</option>
          <option value="RESOLVED">Resolved</option>
        </select>
        <select
          aria-label="Incident severity"
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
        >
          <option value="">All severities</option>
          {Array.from(new Set(query.data?.map((e) => e.severity))).map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <select
          aria-label="Incident category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">All categories</option>
          {Array.from(new Set(query.data?.map((e) => e.category))).map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <select
          aria-label="Incident pipeline"
          value={pipeline}
          onChange={(e) => setPipeline(e.target.value)}
          disabled={!pipelines.data}
        >
          <option value="">All pipelines</option>
          {pipelines.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Incident destination"
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          disabled={!destinations.data}
        >
          <option value="">All destinations</option>
          {destinations.data?.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </FilterBar>
      {(pipelines.isError || destinations.isError) && (
        <p className="muted">
          Resource filters unavailable; incident records remain accessible.
        </p>
      )}
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} />
      ) : query.data.length ? (
        <DataTable
          onRowClick={(e) => setSelected(e)}
          data={query.data.filter(
            (error) =>
              (!filter || error.status === filter) &&
              (!severity || error.severity === severity) &&
              (!category || error.category === category) &&
              (!pipeline || error.pipeline_id === pipeline) &&
              (!destination || error.destination_id === destination),
          )}
          columns={[
            {
              accessorKey: "created_at",
              header: "Timestamp",
              cell: ({ row }) => date(row.original.created_at),
            },
            { accessorKey: "category", header: "Category" },
            {
              accessorKey: "severity",
              header: "Severity",
              cell: ({ row }) => <Status value={row.original.severity} />,
            },
            {
              accessorKey: "message",
              header: "Message",
              cell: ({ row }) => (
                <button
                  className="text-link incident-message"
                  onClick={() => setSelected(row.original)}
                >
                  {row.original.message}
                </button>
              ),
            },
            {
              accessorKey: "status",
              header: "Status",
              cell: ({ row }) => <Status value={row.original.status} />,
            },
            {
              id: "pipeline",
              header: "Action",
              cell: ({ row }) =>
                row.original.destination_id ? (
                  <Link
                    className="text-link"
                    href={`/destinations/${row.original.destination_id}`}
                  >
                    Inspect destination →
                  </Link>
                ) : row.original.pipeline_id ? (
                  <Link
                    className="text-link"
                    href={`/pipelines/${row.original.pipeline_id}`}
                  >
                    Inspect pipeline →
                  </Link>
                ) : (
                  "Unavailable"
                ),
            },
            {
              id: "manage",
              header: "Incident action",
              cell: ({ row }) => (
                <div className="row-actions">
                  {row.original.status === "OPEN" && (
                    <Button
                      variant="ghost"
                      disabled={change.isPending}
                      onClick={() =>
                        change.mutate({
                          id: row.original.id,
                          status: "ACKNOWLEDGED",
                        })
                      }
                    >
                      Acknowledge
                    </Button>
                  )}
                  {row.original.status !== "RESOLVED" && (
                    <Button
                      variant="ghost"
                      disabled={change.isPending}
                      onClick={() =>
                        change.mutate({
                          id: row.original.id,
                          status: "RESOLVED",
                        })
                      }
                    >
                      Resolve
                    </Button>
                  )}
                </div>
              ),
            },
          ]}
        />
      ) : (
        <Empty
          title="No recorded runtime failures"
          description="The reconciler reports connector and task failures here. Restart actions are available on the pipeline detail page."
        />
      )}
      <DetailDrawer
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title="Incident details"
        description={selected?.category.replaceAll("_", " ")}
      >
        {selected && (
          <>
            <div className="row-actions">
              <Status
                value={
                  query.data?.find((e) => e.id === selected.id)?.status ||
                  selected.status
                }
              />
              <Status value={selected.severity} />
            </div>
            <p className="incident-detail-message">{selected.message}</p>
            <dl className="facts">
              <dt>Observed</dt>
              <dd>{date(selected.created_at)}</dd>
              <dt>Connector</dt>
              <dd>{selected.connector_name || "Unavailable"}</dd>
              <dt>Task</dt>
              <dd>{selected.task_id ?? "Unavailable"}</dd>
            </dl>
            {selected.pipeline_id && (
              <Link
                className="text-link"
                href={`/pipelines/${selected.pipeline_id}`}
              >
                Inspect pipeline
              </Link>
            )}
            {selected.destination_id && (
              <Link
                className="text-link"
                href={`/destinations/${selected.destination_id}`}
              >
                Inspect destination
              </Link>
            )}
          </>
        )}
      </DetailDrawer>
    </>
  );
}

export function SchemasPage({
  sourceId,
  schema,
  table,
}: {
  sourceId?: string;
  schema?: string;
  table?: string;
}) {
  const [selected, setSelected] = useState<SchemaVersion | null>(null);
  const query = useQuery({
    queryKey: ["schemas", sourceId],
    queryFn: () =>
      api<SchemaVersion[]>(
        `/data/schemas${sourceId ? `?source_id=${sourceId}` : ""}`,
      ),
  });
  const versions = query.data?.filter(
    (v) =>
      (!schema || v.schema_name === schema) &&
      (!table || v.table_name === table),
  );
  return (
    <>
      <PageHeader
        title={table ? `${schema}.${table}` : "Schema versions"}
        description="Discovered source schemas, deterministic changes, and compatibility classification."
        eyebrow="DATA / SCHEMAS"
      />
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} />
      ) : versions?.length ? (
        <DataTable
          onRowClick={(v) => setSelected(v)}
          data={versions}
          columns={[
            {
              accessorKey: "table_name",
              header: "Table",
              cell: ({ row }) => (
                <Link
                  className="text-link"
                  href={`/data/schemas/${row.original.source_id}/${encodeURIComponent(row.original.schema_name)}/${encodeURIComponent(row.original.table_name)}`}
                >
                  {row.original.schema_name}.{row.original.table_name}
                </Link>
              ),
            },
            { accessorKey: "version", header: "Version" },
            {
              id: "classification",
              header: "Compatibility",
              accessorFn: (v) => changeClassification(v.diff_json),
              cell: ({ row }) => (
                <Status
                  value={
                    row.original.version === 1
                      ? "BASELINE"
                      : changeClassification(row.original.diff_json)
                  }
                />
              ),
            },
            {
              accessorKey: "created_at",
              header: "Discovered",
              cell: ({ row }) => date(row.original.created_at),
            },
            {
              accessorKey: "diff_json",
              header: "Changes",
              cell: ({ row }) => (
                <button
                  className="text-link"
                  onClick={() => setSelected(row.original)}
                >
                  {row.original.diff_json.length} changes · Inspect schema
                </button>
              ),
            },
            {
              accessorKey: "schema_hash",
              header: "Hash",
              cell: ({ row }) => (
                <code>{row.original.schema_hash.slice(0, 14)}…</code>
              ),
            },
          ]}
        />
      ) : (
        <Empty
          title="No schemas discovered"
          description="Run source discovery to save the first schema version. Repeat discovery to detect changes."
          href="/sources"
          action="Open sources"
        />
      )}
      <DetailDrawer
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title={
          selected
            ? `${selected.schema_name}.${selected.table_name} · v${selected.version}`
            : "Schema version"
        }
        description="Nullable additions are non-breaking; removals and primary-key changes are breaking; type changes require review."
      >
        {selected && (
          <>
            <h3>Changes</h3>
            <DataTable
              data={selected.diff_json.flatMap((d) =>
                d && typeof d === "object" && !Array.isArray(d) ? [d] : [],
              )}
              columns={[
                { accessorKey: "column", header: "Column" },
                { accessorKey: "change", header: "Change" },
                {
                  accessorKey: "classification",
                  header: "Compatibility",
                  cell: ({ row }) => (
                    <Status
                      value={String(row.original.classification || "UNKNOWN")}
                    />
                  ),
                },
              ]}
            />
            <details>
              <summary>Raw change details</summary>
              <JsonView value={selected.diff_json} />
            </details>
            <h3>Schema</h3>
            <JsonView value={selected.schema_json} />
          </>
        )}
      </DetailDrawer>
    </>
  );
}

export function SettingsPage() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () =>
      api<{
        actor: string;
        role: string;
        environment: string;
        auth_mode: string;
      }>("/session"),
  });
  return (
    <>
      <PageHeader
        title="Settings"
        description="Authentication, deployment configuration, and integration availability."
        eyebrow="WORKSPACE"
      />
      <div className="detail-grid">
        <section className="panel">
          <h2>Authentication</h2>
          {session.isError ? (
            <ErrorPanel error={session.error} />
          ) : (
            session.data && (
              <dl className="facts">
                <dt>Mode</dt>
                <dd>{session.data.auth_mode}</dd>
                <dt>Actor</dt>
                <dd>{session.data.actor}</dd>
                <dt>Role</dt>
                <dd>{session.data.role}</dd>
                <dt>Environment</dt>
                <dd>{session.data.environment}</dd>
              </dl>
            )
          )}
          <div className="toolbar">
            {session.data?.role === "Admin" && (
              <>
                <Button asChild>
                  <Link href="/settings/users">Manage users</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href="/settings/security">Security policy</Link>
                </Button>
              </>
            )}
          </div>
        </section>
        <section className="panel">
          <h2>Integrations</h2>
          <dl className="facts">
            <dt>Source adapter</dt>
            <dd>PostgreSQL</dd>
            <dt>Capture runtime</dt>
            <dd>Kafka Connect / Debezium</dd>
            <dt>Kafka inspection</dt>
            <dd>Metadata and bounded samples</dd>
            <dt>Secret provider</dt>
            <dd>Encrypted database (Fernet)</dd>
            <dt>Stream metrics</dt>
            <dd>Unavailable · provider interface defined</dd>
            <dt>Application metrics</dt>
            <dd>
              <code>/metrics</code> on the API
            </dd>
          </dl>
          <p className="muted">
            Production deployment and authentication setup instructions are in
            the repository documentation.
          </p>
        </section>
      </div>
    </>
  );
}
