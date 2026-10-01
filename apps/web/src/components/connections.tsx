"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  CheckCircle2,
  Cloud,
  Plus,
  Search,
  Server,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import type {
  Connection,
  ConnectionCategory,
  ConnectionProvider,
  ConnectionProviderMetadata,
  ConnectionTestResult,
  LakehouseTarget,
  Pipeline,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { api, date, post } from "@/lib/api";
import {
  DataTable,
  Empty,
  ErrorPanel,
  Field,
  JsonView,
  Loading,
  PageHeader,
  Status,
} from "./common";

const categoryCopy: Record<
  ConnectionCategory,
  { title: string; description: string; icon: typeof Cloud }
> = {
  DATABASE: {
    title: "Database",
    description: "CDC sources and database delivery targets.",
    icon: Server,
  },
  OBJECT_STORAGE: {
    title: "Object Storage",
    description:
      "Stores physical Lakehouse data files. Examples: S3 and MinIO.",
    icon: Cloud,
  },
};

const providerName = (provider: string) =>
  ({
    POSTGRESQL: "PostgreSQL",
    MYSQL: "MySQL",
    SQL_SERVER: "SQL Server",
    ORACLE: "Oracle",
    AWS_S3: "Amazon S3",
    MINIO: "MinIO",
  })[provider] || provider;

export function ConnectionsPage({
  capability,
}: {
  capability?: "SOURCE" | "DESTINATION";
} = {}) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<ConnectionCategory | "ALL">("ALL");
  const [search, setSearch] = useState("");
  const [deleting, setDeleting] = useState<Connection | null>(null);
  const connections = useQuery({
    queryKey: ["connections", capability],
    queryFn: () =>
      api<Connection[]>(
        `/connections${capability ? `?capability=${capability}` : ""}`,
      ),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/connections/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Connection deleted");
      setDeleting(null);
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const test = useMutation({
    mutationFn: (id: string) =>
      post<ConnectionTestResult>(`/connections/${id}/test`),
    onSuccess: (result) => {
      toast.success(`${result.checks.length} connection checks passed`);
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const columns = useMemo<ColumnDef<Connection>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }) => (
          <div className="resource-name">
            <strong>{row.original.name}</strong>
            <small>{providerName(row.original.provider)}</small>
          </div>
        ),
      },
      {
        accessorKey: "type",
        header: "Type",
        cell: ({ row }) => providerName(row.original.type),
      },
      {
        accessorKey: "category",
        header: "Category",
        cell: ({ row }) => categoryCopy[row.original.category].title,
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row }) => <Status value={row.original.status} />,
      },
      {
        accessorKey: "used_by_count",
        header: "Used by",
        cell: ({ row }) => row.original.used_by_count || 0,
      },
      {
        accessorKey: "last_checked",
        header: "Last checked",
        cell: ({ row }) => date(row.original.last_checked),
      },
      {
        id: "actions",
        header: "Actions",
        cell: ({ row }) => (
          <div
            className="row-actions"
            onClick={(event) => event.stopPropagation()}
          >
            <Button
              variant="ghost"
              disabled={test.isPending}
              onClick={() => test.mutate(row.original.id)}
            >
              Test
            </Button>
            <Button asChild variant="ghost">
              <Link href={`/connections/${row.original.id}`}>Open</Link>
            </Button>
            <Button
              variant="ghost"
              aria-label={`Delete ${row.original.name}`}
              disabled={remove.isPending}
              onClick={() => setDeleting(row.original)}
            >
              <Trash2 size={15} />
            </Button>
          </div>
        ),
      },
    ],
    [remove, test],
  );
  if (connections.isPending) return <Loading />;
  if (connections.isError)
    return (
      <ErrorPanel
        error={connections.error}
        retry={() => connections.refetch()}
      />
    );
  const values = (connections.data || []).filter(
    (value) =>
      (category === "ALL" || value.category === category) &&
      (!search ||
        `${value.name} ${providerName(value.type)} ${value.category}`
          .toLowerCase()
          .includes(search.toLowerCase())),
  );
  const pageTitle = capability
    ? capability === "SOURCE"
      ? "Sources"
      : "Destinations"
    : "All Connections";
  return (
    <>
      <PageHeader
        title={pageTitle}
        description={
          capability
            ? `Filtered view of connections with the ${capability.toLowerCase()} capability.`
            : "Manage every database, object store, catalog, and query engine from one inventory."
        }
        eyebrow="CONNECTIONS"
      >
        <Button asChild>
          <Link href="/connections/new">
            <Plus size={16} /> New Connection
          </Link>
        </Button>
      </PageHeader>
      <div className="filter-bar connection-filter-bar">
        <div className="tabs" role="tablist" aria-label="Connection category">
          {(
            ["ALL", ...(Object.keys(categoryCopy) as ConnectionCategory[])] as (
              ConnectionCategory | "ALL"
            )[]
          ).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={category === value}
              className={category === value ? "active" : ""}
              onClick={() => setCategory(value)}
            >
              {value === "ALL" ? "All" : categoryCopy[value].title}
            </button>
          ))}
        </div>
        <label className="search-field">
          <Search size={15} aria-hidden="true" />
          <Input
            aria-label="Search connections"
            placeholder="Search connections"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>
      {!capability && (
        <section
          className="connection-categories"
          aria-label="Connection categories"
        >
          {(Object.keys(categoryCopy) as ConnectionCategory[]).map(
            (category) => {
              const item = categoryCopy[category];
              const Icon = item.icon;
              return (
                <div className="connection-category" key={category}>
                  <Icon size={20} />
                  <div>
                    <strong>{item.title}</strong>
                    <p>{item.description}</p>
                  </div>
                  <span>
                    {
                      values.filter((value) => value.category === category)
                        .length
                    }
                  </span>
                </div>
              );
            },
          )}
        </section>
      )}
      {values.length ? (
        <DataTable
          data={values}
          columns={columns}
          getRowHref={(row) => `/connections/${row.id}`}
        />
      ) : (
        <Empty
          title="No infrastructure connections"
          description="Add object storage first, then an Iceberg catalog and optional query engine."
          href="/connections/new"
          action="Add connection"
        />
      )}
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete connection?"
        description="This is blocked while pipelines, deliveries, or Lakehouse targets use the connection. Stored credentials will also be removed."
      >
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setDeleting(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => deleting && remove.mutate(deleting.id)}
          >
            Delete connection
          </Button>
        </div>
        {remove.isError && <ErrorPanel error={remove.error} />}
      </Dialog>
    </>
  );
}

type FormState = {
  name: string;
  description: string;
  config: Record<string, string | number | boolean>;
  credentials: Record<string, string>;
};

const initialState = (provider: ConnectionProvider): FormState => {
  if (["POSTGRESQL", "MYSQL", "SQL_SERVER", "ORACLE"].includes(provider)) {
    const ports: Record<string, number> = {
      POSTGRESQL: 5432,
      MYSQL: 3306,
      SQL_SERVER: 1433,
      ORACLE: 1521,
    };
    return {
      name: "",
      description: "",
      config: {
        host: "localhost",
        port: ports[provider],
        database_name: "",
        username: "",
        ssl_enabled: false,
        environment: "DEV",
      },
      credentials: { password: "" },
    };
  }
  if (["AWS_S3", "MINIO"].includes(provider))
    return {
      name: "",
      description: "",
      config: {
        endpoint: provider === "AWS_S3" ? "" : "http://localhost:9000",
        region: "us-east-1",
        bucket: "lakehouse",
        base_path: "warehouse",
        path_style_access: provider !== "AWS_S3",
        ssl_enabled: provider === "AWS_S3",
        verify_write: true,
      },
      credentials: { access_key: "", secret_key: "", session_token: "" },
    };
  throw new Error(`Unsupported connection provider: ${provider}`);
};

const providerCategory = (provider: ConnectionProvider): ConnectionCategory =>
  ["POSTGRESQL", "MYSQL", "SQL_SERVER", "ORACLE"].includes(provider)
    ? "DATABASE"
    : "OBJECT_STORAGE";

export function ConnectionWizard() {
  const router = useRouter();
  const [provider, setProvider] = useState<ConnectionProvider | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [phase, setPhase] = useState(0);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(
    null,
  );
  const providers = useQuery({
    queryKey: ["connection-providers"],
    queryFn: () => api<ConnectionProviderMetadata[]>("/connection-providers"),
  });
  const payload = () => {
    if (!provider || !form) throw new Error("Choose a provider");
    const config = Object.fromEntries(
      Object.entries(form.config).filter(([, value]) => value !== ""),
    );
    const credentials = Object.fromEntries(
      Object.entries(form.credentials).filter(([, value]) => value !== ""),
    );
    return {
      ...form,
      config,
      credentials,
      provider,
      category: providerCategory(provider),
    };
  };
  const create = useMutation({
    mutationFn: () => post<Connection>("/connections", payload()),
    onSuccess: (value) => router.push(`/connections/${value.id}`),
  });
  const testDraft = useMutation({
    mutationFn: () =>
      post<ConnectionTestResult>("/connections/test", payload()),
    onSuccess: (result) => setTestResult(result),
  });
  const select = (value: ConnectionProvider) => {
    setProvider(value);
    setForm(initialState(value));
    setPhase(1);
    setTestResult(null);
  };
  const config = (key: string, value: string | number | boolean) =>
    setForm((old) =>
      old ? { ...old, config: { ...old.config, [key]: value } } : old,
    );
  const secret = (key: string, value: string) =>
    setForm((old) =>
      old ? { ...old, credentials: { ...old.credentials, [key]: value } } : old,
    );
  return (
    <>
      <PageHeader
        title="New Connection"
        description={
          provider
            ? `Configure ${providerName(provider)}.`
            : "What do you want to connect?"
        }
        eyebrow="CONNECTIONS / NEW"
      />
      <div className="wizard-steps" aria-label="Connection creation steps">
        {[
          "Choose Type",
          "Connection Details",
          "Authentication",
          "Advanced Options",
          "Test Connection",
          "Review",
        ].map((label, index) => (
          <div
            key={label}
            className={
              index === phase ? "current" : index < phase ? "done" : ""
            }
          >
            <span>{index + 1}</span>
            {label}
          </div>
        ))}
      </div>
      {!provider || phase === 0 ? (
        providers.isPending ? (
          <Loading />
        ) : providers.isError ? (
          <ErrorPanel error={providers.error} />
        ) : (
          <div className="provider-groups">
            {(Object.keys(categoryCopy) as ConnectionCategory[]).map(
              (category) => (
                <section className="panel" key={category}>
                  <div className="panel-heading">
                    <div>
                      <h2>{categoryCopy[category].title}</h2>
                      <p>{categoryCopy[category].description}</p>
                    </div>
                  </div>
                  <div className="provider-grid">
                    {providers.data
                      ?.filter((item) => item.category === category)
                      .map((item) => (
                        <button
                          key={item.provider}
                          onClick={() => select(item.provider)}
                        >
                          <Server size={20} />
                          <strong>{item.name}</strong>
                          <span>{item.description}</span>
                        </button>
                      ))}
                  </div>
                </section>
              ),
            )}
          </div>
        )
      ) : phase === 1 ? (
        form && (
          <section className="panel source-form-page">
            <div className="panel-body">
              <div className="form-grid" onChange={() => setTestResult(null)}>
                <Field label="Connection name">
                  <Input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </Field>
                <Field label="Description">
                  <Input
                    value={form.description}
                    onChange={(e) =>
                      setForm({ ...form, description: e.target.value })
                    }
                  />
                </Field>
                <ProviderFields
                  provider={provider}
                  form={form}
                  config={config}
                  secret={secret}
                />
              </div>
              <div className="wizard-actions">
                <Button
                  variant="outline"
                  onClick={() => {
                    setProvider(null);
                    setForm(null);
                    setPhase(0);
                  }}
                >
                  Back
                </Button>
                <Button disabled={!form.name} onClick={() => setPhase(4)}>
                  Continue to test
                </Button>
              </div>
            </div>
          </section>
        )
      ) : phase === 4 ? (
        <section className="panel source-form-page">
          <div className="panel-body">
            <h2>Test Connection</h2>
            <p className="muted">
              ClueCDC validates network access, authentication, and
              provider-specific permissions without saving credentials.
            </p>
            {testDraft.isError && <ErrorPanel error={testDraft.error} />}
            {testResult && (
              <div className="diagnostic-list">
                {testResult.checks.map((check) => (
                  <div key={check.name}>
                    <strong>{check.name.replaceAll("_", " ")}</strong>
                    <Status value={check.status} />
                    <span>{check.message || "Passed"}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="wizard-actions">
              <Button variant="outline" onClick={() => setPhase(1)}>
                Back
              </Button>
              <Button
                variant="outline"
                disabled={testDraft.isPending}
                onClick={() => testDraft.mutate()}
              >
                Test Connection
              </Button>
              <Button onClick={() => setPhase(5)}>Review</Button>
            </div>
          </div>
        </section>
      ) : (
        form && (
          <section className="panel source-form-page">
            <div className="panel-body">
              <h2>Review</h2>
              <dl className="facts">
                <dt>Name</dt>
                <dd>{form.name}</dd>
                <dt>Type</dt>
                <dd>{providerName(provider)}</dd>
                <dt>Category</dt>
                <dd>{categoryCopy[providerCategory(provider)].title}</dd>
                <dt>Credentials</dt>
                <dd>Configured and encrypted on save</dd>
              </dl>
              <details className="advanced">
                <summary>Non-sensitive configuration</summary>
                <JsonView value={form.config} />
              </details>
              {create.isError && <ErrorPanel error={create.error} />}
              <div className="wizard-actions">
                <Button variant="outline" onClick={() => setPhase(4)}>
                  Back
                </Button>
                <Button
                  disabled={create.isPending}
                  onClick={() => create.mutate()}
                >
                  Create Connection
                </Button>
              </div>
            </div>
          </section>
        )
      )}
    </>
  );
}

function ProviderFields({
  provider,
  form,
  config,
  secret,
}: {
  provider: ConnectionProvider;
  form: FormState;
  config: (key: string, value: string | number | boolean) => void;
  secret: (key: string, value: string) => void;
}) {
  if (["POSTGRESQL", "MYSQL", "SQL_SERVER", "ORACLE"].includes(provider))
    return (
      <>
        <Field label="Host">
          <Input
            value={String(form.config.host)}
            onChange={(e) => config("host", e.target.value)}
          />
        </Field>
        <Field label="Port">
          <Input
            type="number"
            value={Number(form.config.port)}
            onChange={(e) => config("port", Number(e.target.value))}
          />
        </Field>
        <Field label="Database">
          <Input
            value={String(form.config.database_name)}
            onChange={(e) => config("database_name", e.target.value)}
          />
        </Field>
        <Field label="Username">
          <Input
            value={String(form.config.username)}
            onChange={(e) => config("username", e.target.value)}
          />
        </Field>
        <Field label="Password">
          <Input
            type="password"
            autoComplete="new-password"
            value={form.credentials.password}
            onChange={(e) => secret("password", e.target.value)}
          />
        </Field>
        <Field label="Environment">
          <select
            value={String(form.config.environment)}
            onChange={(e) => config("environment", e.target.value)}
          >
            <option value="DEV">Development</option>
            <option value="STAGING">Staging</option>
            <option value="PROD">Production</option>
          </select>
        </Field>
        <label className="check-row">
          <input
            type="checkbox"
            checked={Boolean(form.config.ssl_enabled)}
            onChange={(e) => config("ssl_enabled", e.target.checked)}
          />{" "}
          TLS enabled
        </label>
      </>
    );
  if (["AWS_S3", "MINIO"].includes(provider))
    return (
      <>
        <Field
          label="Endpoint"
          hint="Optional for Amazon S3; required for other providers."
        >
          <Input
            value={String(form.config.endpoint)}
            onChange={(e) => config("endpoint", e.target.value)}
          />
        </Field>
        <Field label="Region">
          <Input
            value={String(form.config.region)}
            onChange={(e) => config("region", e.target.value)}
          />
        </Field>
        <Field label="Bucket">
          <Input
            value={String(form.config.bucket)}
            onChange={(e) => config("bucket", e.target.value)}
          />
        </Field>
        <Field label="Base path">
          <Input
            value={String(form.config.base_path)}
            onChange={(e) => config("base_path", e.target.value)}
          />
        </Field>
        <Field label="Access key">
          <Input
            autoComplete="off"
            value={form.credentials.access_key}
            onChange={(e) => secret("access_key", e.target.value)}
          />
        </Field>
        <Field label="Secret key">
          <Input
            type="password"
            autoComplete="new-password"
            value={form.credentials.secret_key}
            onChange={(e) => secret("secret_key", e.target.value)}
          />
        </Field>
        <label className="check-row">
          <input
            type="checkbox"
            checked={Boolean(form.config.path_style_access)}
            onChange={(e) => config("path_style_access", e.target.checked)}
          />{" "}
          Path-style access
        </label>
        <label className="check-row">
          <input
            type="checkbox"
            checked={Boolean(form.config.ssl_enabled)}
            onChange={(e) => config("ssl_enabled", e.target.checked)}
          />{" "}
          TLS enabled
        </label>
      </>
    );
  return null;
}

export function ConnectionDetailPage({ id }: { id: string }) {
  const queryClient = useQueryClient();
  const [checks, setChecks] = useState<ConnectionTestResult | null>(null);
  const connection = useQuery({
    queryKey: ["connection", id],
    queryFn: () => api<Connection>(`/connections/${id}`),
  });
  const test = useMutation({
    mutationFn: () => post<ConnectionTestResult>(`/connections/${id}/test`),
    onSuccess: (value) => {
      setChecks(value);
      queryClient.invalidateQueries({ queryKey: ["connection", id] });
    },
  });
  if (connection.isPending) return <Loading />;
  if (connection.isError) return <ErrorPanel error={connection.error} />;
  const value = connection.data;
  return (
    <>
      <PageHeader
        title={value.name}
        description={value.description || providerName(value.provider)}
        eyebrow={`CONNECTIONS / ${categoryCopy[value.category].title.toUpperCase()}`}
      >
        <Button asChild variant="outline">
          <Link href={`/connections/${id}/edit`}>Edit</Link>
        </Button>
        <Button disabled={test.isPending} onClick={() => test.mutate()}>
          Test Connection
        </Button>
      </PageHeader>
      {test.isError && <ErrorPanel error={test.error} />}
      <section className="resource-summary">
        <div className="metric-card">
          <span className="metric-title">Status</span>
          <Status value={value.status} />
        </div>
        <div className="metric-card">
          <span className="metric-title">Provider</span>
          <strong>{providerName(value.provider)}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-title">Last tested</span>
          <strong className="summary-date">{date(value.last_tested_at)}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-title">Credentials</span>
          <strong>
            {value.credentials.configured
              ? value.credentials.masked_value
              : "Not configured"}
          </strong>
        </div>
      </section>
      {checks && (
        <section className="panel">
          <div className="panel-heading">
            <h2>Connection diagnostics</h2>
          </div>
          <div className="diagnostic-list">
            {checks.checks.map((check) => (
              <div key={check.name}>
                <CheckCircle2 size={17} />
                <strong>{check.name.replaceAll("_", " ")}</strong>
                <Status value={check.status} />
                <span>{check.message}</span>
              </div>
            ))}
          </div>
        </section>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Used by</h2>
            <p>
              Pipelines, deliveries, and Lakehouse targets using this
              connection.
            </p>
          </div>
        </div>
        {value.used_by?.length ? (
          <div className="audit-list">
            {value.used_by.map((dependency) => (
              <div key={`${dependency.type}-${dependency.id}`}>
                <Status value={dependency.type} />
                <strong>{dependency.name}</strong>
                <span>
                  {dependency.pipeline || dependency.type.replaceAll("_", " ")}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">
            This connection is not used by another resource.
          </p>
        )}
      </section>
      <details className="advanced">
        <summary>Non-sensitive connection configuration</summary>
        <JsonView value={value.config} />
      </details>
    </>
  );
}

export function ConnectionEditPage({ id }: { id: string }) {
  const connection = useQuery({
    queryKey: ["connection", id],
    queryFn: () => api<Connection>(`/connections/${id}`),
  });
  if (connection.isPending) return <Loading />;
  if (connection.isError) return <ErrorPanel error={connection.error} />;
  return (
    <ConnectionEditor
      key={connection.data.updated_at}
      value={connection.data}
    />
  );
}

function ConnectionEditor({ value }: { value: Connection }) {
  const router = useRouter();
  const defaults = initialState(value.provider);
  const [form, setForm] = useState<FormState>({
    name: value.name,
    description: value.description,
    config: {
      ...defaults.config,
      ...(Object.fromEntries(
        Object.entries(value.config).filter(([, item]) =>
          ["string", "number", "boolean"].includes(typeof item),
        ),
      ) as Record<string, string | number | boolean>),
    },
    credentials: defaults.credentials,
  });
  const save = useMutation({
    mutationFn: () =>
      api<Connection>(`/connections/${value.id}`, {
        method: "PUT",
        body: JSON.stringify({
          ...form,
          credentials: Object.fromEntries(
            Object.entries(form.credentials).filter(([, secret]) => secret),
          ),
          provider: value.provider,
          category: value.category,
          capabilities: value.capabilities,
        }),
      }),
    onSuccess: () => router.push(`/connections/${value.id}`),
  });
  const config = (key: string, next: string | number | boolean) =>
    setForm((old) => ({ ...old, config: { ...old.config, [key]: next } }));
  const secret = (key: string, next: string) =>
    setForm((old) => ({
      ...old,
      credentials: { ...old.credentials, [key]: next },
    }));
  return (
    <>
      <PageHeader
        title={`Edit ${value.name}`}
        description="Update non-sensitive settings or replace selected credentials. Saved secrets are never returned."
        eyebrow="CONNECTIONS / EDIT"
      />
      <section className="panel source-form-page">
        <div className="panel-body">
          <div className="form-grid">
            <Field label="Connection name">
              <Input
                value={form.name}
                onChange={(event) =>
                  setForm({ ...form, name: event.target.value })
                }
              />
            </Field>
            <Field label="Description">
              <Input
                value={form.description}
                onChange={(event) =>
                  setForm({ ...form, description: event.target.value })
                }
              />
            </Field>
            <ProviderFields
              provider={value.provider}
              form={form}
              config={config}
              secret={secret}
            />
          </div>
          {save.isError && <ErrorPanel error={save.error} />}
          <div className="wizard-actions">
            <Button variant="outline" onClick={() => router.back()}>
              Cancel
            </Button>
            <Button
              disabled={!form.name || save.isPending}
              onClick={() => save.mutate()}
            >
              Save changes
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}

export function LegacyLakehouseTargetsPage() {
  const values = useQuery({
    queryKey: ["lakehouse-targets"],
    queryFn: () => api<LakehouseTarget[]>("/lakehouse-targets"),
  });
  const columns: ColumnDef<LakehouseTarget>[] = [
    { accessorKey: "name", header: "Lakehouse" },
    { accessorKey: "namespace", header: "Namespace" },
    { accessorKey: "file_format", header: "File format" },
    { accessorKey: "write_mode", header: "CDC behavior" },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <Status value={row.original.status} />,
    },
    { accessorKey: "delivery_count", header: "Deliveries" },
  ];
  if (values.isPending) return <Loading />;
  if (values.isError) return <ErrorPanel error={values.error} />;
  return (
    <>
      <PageHeader
        title="Lakehouse Targets"
        description="Write CDC data to Apache Iceberg tables on Amazon S3 or MinIO."
        eyebrow="DATA FLOW"
      >
        <Button asChild>
          <Link href="/deliveries/new?type=lakehouse">
            <Plus size={16} /> New Lakehouse
          </Link>
        </Button>
      </PageHeader>
      {values.data?.length ? (
        <DataTable
          data={values.data}
          columns={columns}
          getRowHref={(row) =>
            `/deliveries/new?type=lakehouse&target_id=${row.id}`
          }
        />
      ) : (
        <Empty
          title="No Lakehouse targets"
          description="Create an Amazon S3 or MinIO connection, then configure the Iceberg target here."
          href="/deliveries/new?type=lakehouse"
          action="Create Lakehouse"
        />
      )}
    </>
  );
}

export function LakehouseWizard({
  deliveryFirst = false,
}: { deliveryFirst?: boolean } = {}) {
  const router = useRouter();
  const params = useSearchParams();
  const connections = useQuery({
    queryKey: ["connections"],
    queryFn: () => api<Connection[]>("/connections"),
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
    enabled: deliveryFirst,
  });
  const [form, setForm] = useState({
    name: "",
    description: "",
    storage_connection_id: "",
    warehouse: "s3://lakehouse/warehouse",
    namespace: "production",
    file_format: "PARQUET",
    write_mode: "UPSERT",
    delete_mode: "PROPAGATE",
    schema_evolution: true,
    auto_create_tables: true,
    pipeline_id: params.get("pipeline_id") || "",
  });
  const create = useMutation({
    mutationFn: async () => {
      const target = await post<LakehouseTarget>("/lakehouse-targets", {
        ...form,
        pipeline_id: undefined,
      });
      if (!deliveryFirst) return target;
      return post<{ id: string }>(`/lakehouse-targets/${target.id}/deploy`, {
        pipeline_id: form.pipeline_id,
        name: form.name,
      });
    },
    onSuccess: (value) =>
      router.push(
        deliveryFirst
          ? `/deliveries/${value.id}`
          : `/lakehouse-targets/${value.id}`,
      ),
  });
  if (connections.isPending) return <Loading />;
  if (connections.isError) return <ErrorPanel error={connections.error} />;
  const options = (category: ConnectionCategory) =>
    connections.data.filter((item) => item.category === category);
  if (!options("OBJECT_STORAGE").length)
    return (
      <>
        <PageHeader
          title="Create Lakehouse Delivery"
          description="An object store is required before a Lakehouse can be created."
        />
        <Empty
          title="No Object Storage connections"
          description="Lakehouse deliveries need an object store for physical data files."
          href="/connections/new"
          action="Add Object Storage"
        />
      </>
    );
  const set = (key: string, value: string | boolean) =>
    setForm((old) => ({ ...old, [key]: value }));
  return (
    <>
      <PageHeader
        title={
          deliveryFirst
            ? "Create Lakehouse Delivery"
            : "Create Lakehouse Target"
        }
        description="Choose Amazon S3 or MinIO storage. Iceberg metadata is managed by the built-in Hadoop catalog."
        eyebrow="LAKEHOUSE / NEW"
      />
      <section className="panel source-form-page">
        <div className="panel-body">
          <div className="form-grid">
            {deliveryFirst && (
              <Field label="Source pipeline">
                <select
                  value={form.pipeline_id}
                  onChange={(e) => set("pipeline_id", e.target.value)}
                >
                  <option value="">Select pipeline</option>
                  {pipelines.data
                    ?.filter((item) => item.connector_id)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                </select>
              </Field>
            )}
            <Field label="Name">
              <Input
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
              />
            </Field>
            <Field label="Table format">
              <select disabled>
                <option>Apache Iceberg</option>
              </select>
            </Field>
            <Field label="Object Storage">
              <select
                value={form.storage_connection_id}
                onChange={(e) => set("storage_connection_id", e.target.value)}
              >
                <option value="">Select storage</option>
                {options("OBJECT_STORAGE").map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Warehouse">
              <Input
                value={form.warehouse}
                onChange={(e) => set("warehouse", e.target.value)}
              />
            </Field>
            <Field label="Namespace">
              <Input
                value={form.namespace}
                onChange={(e) => set("namespace", e.target.value)}
              />
            </Field>
            <Field label="File format">
              <select
                value={form.file_format}
                onChange={(e) => set("file_format", e.target.value)}
              >
                <option value="PARQUET">Parquet</option>
                <option value="ORC">ORC</option>
              </select>
            </Field>
            <Field label="CDC write mode">
              <select
                value={form.write_mode}
                onChange={(e) => set("write_mode", e.target.value)}
              >
                <option value="UPSERT">Upsert</option>
                <option value="APPEND_ONLY">Append only</option>
              </select>
            </Field>
            <label className="check-row">
              <input
                type="checkbox"
                checked={form.auto_create_tables}
                onChange={(e) => set("auto_create_tables", e.target.checked)}
              />{" "}
              Automatic table creation
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={form.schema_evolution}
                onChange={(e) => set("schema_evolution", e.target.checked)}
              />{" "}
              Schema evolution
            </label>
          </div>
          {create.isError && <ErrorPanel error={create.error} />}
          <div className="wizard-actions">
            <Button
              disabled={
                !form.name ||
                !form.storage_connection_id ||
                (deliveryFirst && !form.pipeline_id) ||
                create.isPending
              }
              onClick={() => create.mutate()}
            >
              {deliveryFirst ? "Create Delivery" : "Save Lakehouse Target"}
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}

export function LegacyLakehouseTargetDetail({ id }: { id: string }) {
  const [pipelineId, setPipelineId] = useState("");
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const destination = useQuery({
    queryKey: ["lakehouse-destination", id],
    queryFn: () => api<LakehouseTarget>(`/lakehouse-targets/${id}`),
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
  });
  const deploy = useMutation({
    mutationFn: () =>
      post(`/lakehouse-targets/${id}/deploy`, {
        pipeline_id: pipelineId,
        name: "Iceberg delivery",
      }),
    onSuccess: () => toast.success("Iceberg delivery deployed"),
  });
  const review = useMutation({
    mutationFn: () =>
      post<Record<string, unknown>>(
        `/lakehouse-targets/${id}/preview-delivery`,
        { pipeline_id: pipelineId, name: "Iceberg delivery" },
      ),
    onSuccess: setPreview,
  });
  if (destination.isPending) return <Loading />;
  if (destination.isError) return <ErrorPanel error={destination.error} />;
  const value = destination.data;
  return (
    <>
      <PageHeader
        title={value.name}
        description={`${value.table_format} · ${value.namespace} · ${value.write_mode}`}
        eyebrow="LAKEHOUSE TARGET"
      >
        <Status value={value.status} />
      </PageHeader>
      <section className="lakehouse-composition">
        <div>
          <Cloud size={21} />
          <small>OBJECT STORAGE</small>
          <strong>{value.storage_connection.name}</strong>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Deploy Iceberg Delivery</h2>
            <p>
              Creates and validates an Apache Iceberg sink in the
              pipeline&apos;s Kafka Connect cluster.
            </p>
          </div>
        </div>
        <div className="inline-form">
          <Field label="Capture pipeline">
            <select
              value={pipelineId}
              onChange={(e) => {
                setPipelineId(e.target.value);
                setPreview(null);
              }}
            >
              <option value="">Select pipeline</option>
              {pipelines.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Button
            variant="outline"
            disabled={!pipelineId || review.isPending}
            onClick={() => review.mutate()}
          >
            Review Config
          </Button>
          <Button
            disabled={!pipelineId || !preview || deploy.isPending}
            onClick={() => deploy.mutate()}
          >
            Deploy
          </Button>
        </div>
        {review.isError && <ErrorPanel error={review.error} />}
        {deploy.isError && <ErrorPanel error={deploy.error} />}
        {preview && <JsonView value={preview} />}
      </section>
    </>
  );
}
