"use client";
import { InventoryStrip } from "./operational";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Database,
  Plus,
  Rocket,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import type {
  Audit,
  ConnectCluster,
  Delivery,
  DeliveryConfiguration,
  Destination,
  DestinationRuntime,
  Pipeline,
  PipelineDetail,
  TopicMapping,
  DatabaseProviderMetadata,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { api, ApiError, date, post } from "@/lib/api";
import { destinationSchema, type DestinationForm } from "@/lib/validation";
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

const initialConnection: DestinationForm = {
  name: "",
  description: "",
  type: "postgresql",
  environment: "DEV",
  host: "",
  port: 5432,
  database_name: "",
  username: "",
  password: "",
  ssl_enabled: false,
  provider_options: { connection_timeout_seconds: 10 },
};
const initialDelivery: DeliveryConfiguration = {
  pipeline_id: "",
  name: "Primary delivery",
  write_mode: "upsert",
  primary_key_mode: "record_key",
  auto_create: true,
  auto_evolve: true,
  delete_enabled: true,
  null_handling: "ignore",
  batch_size: 500,
  max_retries: 5,
  retry_backoff_ms: 1000,
  tasks_max: 1,
};
function Unavailable({
  notice = "A sink metrics provider is required to measure successful writes.",
}: {
  notice?: string;
}) {
  return (
    <span className="muted" title={notice}>
      Unavailable
    </span>
  );
}

const databaseName = (type: Destination["type"]) =>
  type === "mysql" ? "MySQL" : "PostgreSQL";

function ConnectionFields({
  form,
  existing = false,
}: {
  form: ReturnType<typeof useForm<DestinationForm>>;
  existing?: boolean;
}) {
  const errors = form.formState.errors;
  const providers = useQuery({
    queryKey: ["database-providers"],
    queryFn: () => api<DatabaseProviderMetadata[]>("/database-providers"),
  });
  return (
    <div className="form-grid">
      <Field label="Destination name" error={errors.name?.message}>
        <Input {...form.register("name")} />
      </Field>
      <Field label="Environment">
        <select {...form.register("environment")}>
          <option>DEV</option>
          <option>STAGING</option>
          <option>PROD</option>
        </select>
      </Field>
      <Field label="Database type">
        <select
          {...form.register("type")}
          disabled={existing}
          onChange={(event) => {
            const type = event.target.value as DestinationForm["type"];
            form.setValue("type", type);
            const provider = providers.data?.find((item) => item.type === type);
            if (provider) form.setValue("port", provider.default_port);
          }}
        >
          {(providers.data || [])
            .filter((item) => item.destination_supported)
            .map((provider) => (
              <option key={provider.type} value={provider.type}>
                {provider.display_name}
              </option>
            ))}
        </select>
      </Field>
      <Field label="Host" error={errors.host?.message}>
        <Input {...form.register("host")} placeholder="destination-postgres" />
      </Field>
      <Field label="Port" error={errors.port?.message}>
        <Input
          type="number"
          {...form.register("port", { valueAsNumber: true })}
        />
      </Field>
      <Field label="Database" error={errors.database_name?.message}>
        <Input {...form.register("database_name")} placeholder="analytics" />
      </Field>
      <Field label="Username" error={errors.username?.message}>
        <Input
          {...form.register("username")}
          placeholder="delivery_user"
          autoComplete="off"
        />
      </Field>
      <Field
        label="Password"
        error={errors.password?.message}
        hint={
          existing
            ? "Leave blank to keep the current password."
            : "Encrypted in metadata; never returned by the API."
        }
      >
        <Input
          type="password"
          {...form.register("password")}
          autoComplete="new-password"
        />
      </Field>
      <Field label="SSL">
        <select
          value={form.watch("ssl_enabled") ? "on" : "off"}
          onChange={(event) =>
            form.setValue("ssl_enabled", event.target.value === "on")
          }
        >
          <option value="off">Off</option>
          <option value="on">Verify certificate and hostname</option>
        </select>
      </Field>
      <div className="full-width">
        <Field label="Description">
          <Input {...form.register("description")} />
        </Field>
      </div>
    </div>
  );
}

export function DestinationsPage() {
  const client = useQueryClient();
  const [environment, setEnvironment] = useState("");
  const [state, setState] = useState("");
  const query = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api<Destination[]>("/destinations"),
    refetchInterval: 10000,
  });
  const [deleting, setDeleting] = useState<Destination | null>(null);
  const operation = useMutation({
    mutationFn: ({ id, op }: { id: string; op: string }) =>
      op === "delete"
        ? api(`/destinations/${id}`, { method: "DELETE" })
        : post(`/destinations/${id}/${op}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["destinations"] });
      setDeleting(null);
      toast.success("Destination request completed");
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <>
      <PageHeader
        title="Destinations"
        description="Deliver Kafka CDC streams to downstream systems."
        eyebrow="DATA MOVEMENT / DELIVER"
      >
        <Button asChild>
          <Link href="/destinations/new">
            <Plus size={16} />
            Add destination
          </Link>
        </Button>
      </PageHeader>
      {query.data && (
        <InventoryStrip
          items={[
            { label: "Destinations", value: query.data.length },
            {
              label: "Configured deliveries",
              value: query.data.reduce((n, d) => n + d.delivery_count, 0),
            },
            {
              label: "Running",
              value: query.data.filter((d) => d.actual_state === "RUNNING")
                .length,
            },
            {
              label: "Require attention",
              value: query.data.filter((d) =>
                ["FAILED", "DEGRADED"].includes(d.actual_state),
              ).length,
            },
          ]}
        />
      )}
      <div className="filter-bar">
        <select
          aria-label="Filter destination environment"
          value={environment}
          onChange={(event) => setEnvironment(event.target.value)}
        >
          <option value="">All environments</option>
          {["DEV", "STAGING", "PROD"].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <select
          aria-label="Filter delivery state"
          value={state}
          onChange={(event) => setState(event.target.value)}
        >
          <option value="">All delivery states</option>
          {["RUNNING", "DEGRADED", "FAILED", "PAUSED", "UNKNOWN"].map(
            (value) => (
              <option key={value}>{value}</option>
            ),
          )}
        </select>
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : !query.data.length ? (
        <Empty
          title="No destinations yet"
          description="Destinations deliver CDC data from Kafka into PostgreSQL or MySQL targets."
          href="/destinations/new"
          action="Add destination"
        />
      ) : (
        <DataTable
          getRowHref={(d) => `/destinations/${d.id}`}
          data={query.data.filter(
            (target) =>
              (!environment || target.environment === environment) &&
              (!state || target.actual_state === state),
          )}
          columns={[
            {
              accessorKey: "name",
              header: "Destination",
              cell: ({ row }) => (
                <Link
                  className="entity-link"
                  href={`/destinations/${row.original.id}`}
                >
                  <span className="table-icon">
                    <Database size={16} />
                  </span>
                  <div>
                    {row.original.name}
                    <small>{row.original.database_name}</small>
                  </div>
                </Link>
              ),
            },
            {
              accessorKey: "type",
              header: "Type",
              cell: ({ row }) => databaseName(row.original.type),
            },
            { accessorKey: "environment", header: "Environment" },
            { accessorKey: "connected_pipelines", header: "Pipelines" },
            {
              id: "topics",
              header: "Topics",
              accessorFn: (d) =>
                new Set(
                  d.deliveries.flatMap((l) =>
                    l.topic_mapping_json.map((m) => m.topic),
                  ),
                ).size,
            },
            {
              accessorKey: "actual_state",
              header: "Delivery status",
              cell: ({ row }) => <Status value={row.original.actual_state} />,
            },
            {
              accessorKey: "last_delivery",
              header: "Last delivery",
              cell: ({ row }) =>
                row.original.last_delivery ? (
                  date(row.original.last_delivery)
                ) : (
                  <Unavailable />
                ),
            },
            {
              accessorKey: "status",
              header: "Health",
              cell: ({ row }) => <Status value={row.original.status} />,
            },
            {
              id: "actions",
              header: "Actions",
              cell: ({ row }) => (
                <div className="row-actions">
                  <Button asChild variant="ghost">
                    <Link href={`/destinations/${row.original.id}?edit=true`}>
                      Edit
                    </Link>
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={operation.isPending}
                    onClick={() =>
                      operation.mutate({ id: row.original.id, op: "test" })
                    }
                  >
                    Test
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={
                      operation.isPending || !row.original.delivery_count
                    }
                    onClick={() =>
                      operation.mutate({ id: row.original.id, op: "pause" })
                    }
                  >
                    Pause
                  </Button>
                  <Button
                    variant="ghost"
                    aria-label={`Delete ${row.original.name}`}
                    onClick={() => setDeleting(row.original)}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              ),
            },
          ]}
        />
      )}
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title="Delete destination?"
        description="Remove its deliveries first. Destination rows and Kafka topics are retained."
      >
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setDeleting(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={operation.isPending}
            onClick={() =>
              deleting && operation.mutate({ id: deleting.id, op: "delete" })
            }
          >
            Delete destination
          </Button>
        </div>
        {operation.isError && <ErrorPanel error={operation.error} />}
      </Dialog>
    </>
  );
}

const steps = [
  "Destination",
  "Connection",
  "Delivery source",
  "Mapping",
  "Options",
  "Review",
];
export function DestinationWizard({
  deliveryFirst = false,
}: { deliveryFirst?: boolean } = {}) {
  const router = useRouter();
  const params = useSearchParams();
  const client = useQueryClient();
  const providers = useQuery({
    queryKey: ["database-providers"],
    queryFn: () => api<DatabaseProviderMetadata[]>("/database-providers"),
  });
  const [step, setStep] = useState(params.get("destination_id") ? 2 : 0);
  const [destinationId, setDestinationId] = useState(
    params.get("destination_id") || "",
  );
  const [selectedDestinationId, setSelectedDestinationId] = useState(
    params.get("destination_id") || "",
  );
  const [pipelineId, setPipelineId] = useState(params.get("pipeline_id") || "");
  const [mappings, setMappings] = useState<TopicMapping[]>([]);
  const [automatic, setAutomatic] = useState(true);
  const [options, setOptions] = useState(initialDelivery);
  const [preview, setPreview] = useState<{
    config: Record<string, string>;
  } | null>(null);
  const [tested, setTested] = useState(false);
  const connection = useForm<DestinationForm>({
    resolver: zodResolver(destinationSchema),
    defaultValues: initialConnection,
  });
  const selectedDestinationType = useWatch({
    control: connection.control,
    name: "type",
  });
  const existing = useQuery({
    queryKey: ["destination", destinationId],
    queryFn: () => api<Destination>(`/destinations/${destinationId}`),
    enabled: !!destinationId,
  });
  const registeredDestinations = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api<Destination[]>("/destinations"),
    enabled: step === 0,
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
  });
  const pipeline = useQuery({
    queryKey: ["pipeline", pipelineId],
    queryFn: () => api<PipelineDetail>(`/pipelines/${pipelineId}`),
    enabled: !!pipelineId,
  });
  const clusters = useQuery({
    queryKey: ["connect-clusters"],
    queryFn: () => api<ConnectCluster[]>("/connect/clusters"),
  });
  const payload = () => ({ ...options, pipeline_id: pipelineId, mappings });
  const test = useMutation({
    mutationFn: () =>
      post("/destinations/test-connection", connection.getValues()),
    onSuccess: () => {
      setTested(true);
      toast.success("Destination connection succeeded");
    },
  });
  const save = useMutation({
    mutationFn: () =>
      post<Destination>("/destinations", connection.getValues()),
    onSuccess: (target) => {
      setDestinationId(target.id);
      connection.setValue("password", "");
      client.invalidateQueries({ queryKey: ["destinations"] });
      setStep(2);
    },
  });
  const review = useMutation({
    mutationFn: () =>
      post<{ config: Record<string, string> }>(
        `/destinations/${destinationId}/preview`,
        payload(),
      ),
    onSuccess: (value) => {
      setPreview(value);
      setStep(5);
    },
  });
  const prepareTopics = useMutation({
    mutationFn: () => post(`/pipelines/${pipelineId}/prepare-topics`),
    onSuccess: () => review.mutate(),
  });
  const deploy = useMutation({
    mutationFn: () =>
      post<Delivery>(`/destinations/${destinationId}/deploy`, payload()),
    onSuccess: (created) => {
      client.invalidateQueries({ queryKey: ["destinations"] });
      client.invalidateQueries({ queryKey: ["pipeline"] });
      toast.success("Delivery deployed; observing runtime state");
      router.push(
        deliveryFirst
          ? `/deliveries/${created.id}`
          : `/destinations/${destinationId}`,
      );
    },
  });
  const pending =
    test.isPending ||
    save.isPending ||
    review.isPending ||
    deploy.isPending ||
    prepareTopics.isPending;
  const next = async () => {
    if (step === 0 && selectedDestinationId) {
      setDestinationId(selectedDestinationId);
      setStep(2);
      return;
    }
    if (step === 1) {
      if (!(await connection.trigger())) return;
      if (!connection.getValues("password")) {
        connection.setError("password", { message: "Password is required" });
        return;
      }
      if (!tested) {
        test.mutate();
        return;
      }
      save.mutate();
      return;
    }
    if (step === 2 && (!pipelineId || !mappings.length)) return;
    if (step === 4) {
      review.mutate();
      return;
    }
    setStep((value) => value + 1);
  };
  const chosenName =
    registeredDestinations.data?.find(
      (target) => target.id === selectedDestinationId,
    )?.name ||
    existing.data?.name ||
    connection.getValues("name");
  const chosenDestination =
    registeredDestinations.data?.find(
      (target) => target.id === selectedDestinationId,
    ) || existing.data;
  const targetNamespace =
    (chosenDestination?.type || connection.getValues("type")) === "mysql"
      ? chosenDestination?.database_name ||
        connection.getValues("database_name")
      : "public";
  return (
    <>
      <Link
        href={deliveryFirst ? "/deliveries" : "/destinations"}
        className="back-link"
      >
        <ArrowLeft size={14} />
        {deliveryFirst ? "All deliveries" : "All destinations"}
      </Link>
      <PageHeader
        title={
          deliveryFirst
            ? "Create Delivery"
            : destinationId && step > 1
              ? "Add delivery"
              : "Add destination"
        }
        description="Connect a target, select capture topics, and deploy a managed delivery."
        eyebrow="COMPONENTS / DELIVERY"
      />
      <div className="wizard-steps">
        {steps.map((label, index) => (
          <div
            key={label}
            className={index === step ? "current" : index < step ? "done" : ""}
          >
            <span>{index < step ? <Check size={13} /> : index + 1}</span>
            {label}
          </div>
        ))}
      </div>
      <div className="wizard-layout">
        <section className="panel wizard-panel">
          <h2>{steps[step]}</h2>
          {step === 0 && (
            <>
              <p className="muted">
                Choose a registered destination or add a new destination for
                this delivery.
              </p>
              <Field
                label="Destination"
                hint="Existing destinations reuse their saved connection. Choose topics and mappings in the next steps."
              >
                <select
                  value={selectedDestinationId}
                  onChange={(event) => {
                    setSelectedDestinationId(event.target.value);
                    setDestinationId("");
                    setPreview(null);
                  }}
                >
                  <option value="">Add a new destination</option>
                  {registeredDestinations.data
                    ?.slice()
                    .sort((a, b) => a.name.localeCompare(b.name))
                    .map((target) => (
                      <option key={target.id} value={target.id}>
                        {target.name} · {databaseName(target.type)} ·{" "}
                        {target.environment}
                      </option>
                    ))}
                </select>
              </Field>
              {registeredDestinations.isPending && (
                <p className="muted" role="status">
                  Loading registered destinations…
                </p>
              )}
              {registeredDestinations.isError && (
                <ErrorPanel
                  error={registeredDestinations.error}
                  retry={() => registeredDestinations.refetch()}
                />
              )}
              {registeredDestinations.isSuccess &&
                !registeredDestinations.data.length && (
                  <p className="muted">
                    No destinations registered yet. Add your first destination
                    below.
                  </p>
                )}
              {selectedDestinationId ? (
                <div className="info-strip">
                  <Database size={17} />
                  <span>
                    The saved connection will be reused. No new destination will
                    be created.
                  </span>
                </div>
              ) : (
                <div className="destination-types">
                  {(providers.data || [])
                    .filter((provider) => provider.destination_supported)
                    .map((provider) => (
                      <button
                        key={provider.type}
                        type="button"
                        className={
                          selectedDestinationType === provider.type
                            ? "destination-type selected"
                            : "destination-type"
                        }
                        aria-pressed={selectedDestinationType === provider.type}
                        onClick={() => {
                          connection.setValue("type", provider.type);
                          connection.setValue("port", provider.default_port);
                        }}
                      >
                        <Database size={22} />
                        <strong>{provider.display_name}</strong>
                        <small>Available</small>
                        {selectedDestinationType === provider.type && (
                          <Check size={16} />
                        )}
                      </button>
                    ))}
                </div>
              )}
            </>
          )}
          {step === 1 && (
            <>
              <div onChange={() => setTested(false)}>
                <ConnectionFields form={connection} />
              </div>
              <div className="connection-test">
                <Button
                  variant="outline"
                  disabled={pending}
                  onClick={async () => {
                    if (await connection.trigger()) {
                      if (!connection.getValues("password"))
                        connection.setError("password", {
                          message: "Password is required",
                        });
                      else test.mutate();
                    }
                  }}
                >
                  Test connection
                </Button>
                {tested && <Status value="HEALTHY" />}
              </div>
              <p className="muted">
                The connection is tested before saving. A saved target remains
                unconfigured until deployment succeeds.
              </p>
            </>
          )}
          {step === 2 && (
            <>
              <Field label="Pipeline">
                <select
                  value={pipelineId}
                  onChange={(event) => {
                    setPipelineId(event.target.value);
                    setMappings([]);
                    setOptions((value) => ({
                      ...value,
                      connect_cluster_id: undefined,
                    }));
                  }}
                >
                  <option value="">Select capture pipeline</option>
                  {pipelines.data
                    ?.filter((value) => value.connector_id)
                    .map((value) => (
                      <option key={value.id} value={value.id}>
                        {value.name}
                      </option>
                    ))}
                </select>
              </Field>
              {pipelines.isError && <ErrorPanel error={pipelines.error} />}
              {pipelineId &&
                (pipeline.isPending ? (
                  <Loading />
                ) : pipeline.isError ? (
                  <ErrorPanel error={pipeline.error} />
                ) : (
                  <div className="topic-choices">
                    <h3>Kafka topics</h3>
                    <p className="muted">
                      Select the pipeline topics to deliver. Topic existence is
                      verified during review.
                    </p>
                    {pipeline.data.tables.map((table) => (
                      <label key={table.topic_name}>
                        <input
                          type="checkbox"
                          checked={mappings.some(
                            (mapping) => mapping.topic === table.topic_name,
                          )}
                          onChange={(event) =>
                            setMappings((values) =>
                              event.target.checked
                                ? [
                                    ...values,
                                    {
                                      topic: table.topic_name,
                                      schema_name: targetNamespace,
                                      table_name: table.table_name,
                                    },
                                  ]
                                : values.filter(
                                    (mapping) =>
                                      mapping.topic !== table.topic_name,
                                  ),
                            )
                          }
                        />
                        <span>
                          <strong>{table.topic_name}</strong>
                          <small>
                            {table.schema_name}.{table.table_name}
                          </small>
                        </span>
                      </label>
                    ))}
                  </div>
                ))}
              {!pipelines.isPending &&
                !pipelines.data?.some((value) => value.connector_id) && (
                  <Empty
                    title="Deploy a capture pipeline first"
                    description="Destinations consume real Kafka topics belonging to a capture pipeline."
                    href="/pipelines/new"
                    action="Create pipeline"
                  />
                )}
            </>
          )}
          {step === 3 && (
            <>
              <label className="check-line">
                <input
                  type="checkbox"
                  checked={automatic}
                  onChange={(event) => {
                    setAutomatic(event.target.checked);
                    if (event.target.checked)
                      setMappings((values) =>
                        values.map((mapping) => ({
                          ...mapping,
                          schema_name: targetNamespace,
                          table_name:
                            pipeline.data?.tables.find(
                              (table) => table.topic_name === mapping.topic,
                            )?.table_name || mapping.table_name,
                        })),
                      );
                  }}
                />
                Use topic table name automatically
              </label>
              <p className="muted">
                Namespaces must already exist. Use lowercase portable
                identifiers with letters, numbers, and underscores.
              </p>
              <div className="mapping-editor">
                {mappings.map((mapping, index) => (
                  <div className="mapping-row" key={mapping.topic}>
                    <strong>{mapping.topic}</strong>
                    <ArrowRight size={16} />
                    <Field label={`Schema for ${mapping.topic}`}>
                      <Input
                        disabled={automatic}
                        value={mapping.schema_name}
                        onChange={(event) =>
                          setMappings((values) =>
                            values.map((value, i) =>
                              i === index
                                ? { ...value, schema_name: event.target.value }
                                : value,
                            ),
                          )
                        }
                      />
                    </Field>
                    <Field label={`Table for ${mapping.topic}`}>
                      <Input
                        disabled={automatic}
                        value={mapping.table_name}
                        onChange={(event) =>
                          setMappings((values) =>
                            values.map((value, i) =>
                              i === index
                                ? { ...value, table_name: event.target.value }
                                : value,
                            ),
                          )
                        }
                      />
                    </Field>
                  </div>
                ))}
              </div>
            </>
          )}
          {step === 4 && (
            <>
              <div className="form-grid">
                <Field label="Delivery name">
                  <Input
                    value={options.name}
                    onChange={(event) =>
                      setOptions((value) => ({
                        ...value,
                        name: event.target.value,
                      }))
                    }
                  />
                </Field>
                <Field
                  label="Write mode"
                  hint="Upsert is idempotent. Insert can fail when records are replayed."
                >
                  <select
                    value={options.write_mode}
                    onChange={(event) =>
                      setOptions((value) => ({
                        ...value,
                        write_mode: event.target.value as "upsert" | "insert",
                      }))
                    }
                  >
                    <option value="upsert">Upsert</option>
                    <option value="insert">Insert</option>
                  </select>
                </Field>
                <Field label="Primary key mode">
                  <select value="record_key" disabled>
                    <option value="record_key">Record key</option>
                  </select>
                </Field>
                <Field label="Auto create table">
                  <select
                    value={options.auto_create ? "on" : "off"}
                    onChange={(event) =>
                      setOptions((value) => ({
                        ...value,
                        auto_create: event.target.value === "on",
                        auto_evolve:
                          event.target.value === "on" && value.auto_evolve,
                      }))
                    }
                  >
                    <option value="on">On</option>
                    <option value="off">Off</option>
                  </select>
                </Field>
                <Field
                  label="Auto evolve schema"
                  hint="Adds columns only; never drops or changes column types."
                >
                  <select
                    disabled={!options.auto_create}
                    value={options.auto_evolve ? "on" : "off"}
                    onChange={(event) =>
                      setOptions((value) => ({
                        ...value,
                        auto_evolve: event.target.value === "on",
                      }))
                    }
                  >
                    <option value="on">On</option>
                    <option value="off">Off</option>
                  </select>
                </Field>
              </div>
              <details className="advanced">
                <summary>Advanced delivery options</summary>
                <div className="form-grid">
                  <Field label="Connect cluster">
                    <select
                      value={
                        options.connect_cluster_id ||
                        pipeline.data?.connect_cluster_id ||
                        ""
                      }
                      onChange={(event) =>
                        setOptions((value) => ({
                          ...value,
                          connect_cluster_id: event.target.value,
                        }))
                      }
                    >
                      {clusters.data
                        ?.filter(
                          (cluster) =>
                            cluster.kafka_cluster_id ===
                            pipeline.data?.kafka_cluster_id,
                        )
                        .map((cluster) => (
                          <option key={cluster.id} value={cluster.id}>
                            {cluster.name}
                          </option>
                        ))}
                    </select>
                  </Field>
                  {(
                    [
                      ["batch_size", "Batch size"],
                      ["max_retries", "Max retries"],
                      ["retry_backoff_ms", "Retry backoff (ms)"],
                      ["tasks_max", "Max tasks"],
                    ] as const
                  ).map(([key, label]) => (
                    <Field key={key} label={label}>
                      <Input
                        type="number"
                        min={key === "max_retries" ? 0 : 1}
                        value={options[key]}
                        onChange={(event) =>
                          setOptions((value) => ({
                            ...value,
                            [key]: Number(event.target.value),
                          }))
                        }
                      />
                    </Field>
                  ))}
                  <Field label="Delete handling">
                    <select
                      value={options.delete_enabled ? "delete" : "ignore"}
                      onChange={(event) =>
                        setOptions((value) => ({
                          ...value,
                          delete_enabled: event.target.value === "delete",
                        }))
                      }
                    >
                      <option value="delete">
                        Delete matching destination rows
                      </option>
                      <option value="ignore">Ignore source deletes</option>
                    </select>
                  </Field>
                  <Field
                    label="Null handling"
                    hint="Null tombstones are skipped. DELETE envelopes handle row deletion."
                  >
                    <select disabled>
                      <option>Ignore tombstones</option>
                    </select>
                  </Field>
                </div>
              </details>
              <p className="muted">
                Schema changes require refreshed source discovery and delivery
                mappings. Unsupported types fail validation.
              </p>
            </>
          )}
          {step === 5 && (
            <>
              <div className="review-grid">
                <div>
                  <small>Destination</small>
                  <strong>{chosenName}</strong>
                </div>
                <div>
                  <small>Source pipeline</small>
                  <strong>{pipeline.data?.name}</strong>
                </div>
                <div>
                  <small>Write mode</small>
                  <strong>{options.write_mode}</strong>
                </div>
                <div>
                  <small>Topics</small>
                  <strong>{mappings.length}</strong>
                </div>
              </div>
              <h3>Mappings</h3>
              <DataTable
                search={false}
                data={mappings}
                columns={[
                  { accessorKey: "topic", header: "Kafka topic" },
                  {
                    id: "table",
                    header: "Destination table",
                    cell: ({ row }) =>
                      `${row.original.schema_name}.${row.original.table_name}`,
                  },
                ]}
              />
              <div className="info-strip">
                Connection, topic existence, installed plugin, target readiness,
                and connector configuration validated. Deployment checks them
                again.
              </div>
              <details className="advanced">
                <summary>
                  Advanced · Generated sink connector configuration
                </summary>
                <JsonView value={preview?.config} />
              </details>
            </>
          )}
          {[test, save, review, deploy, prepareTopics].map(
            (mutation, index) =>
              mutation.isError && (
                <ErrorPanel key={index} error={mutation.error} />
              ),
          )}
          {step === 4 &&
            review.error instanceof ApiError &&
            review.error.code === "TOPIC_NOT_FOUND" && (
              <div className="info-strip">
                <span>
                  Empty source tables may have no Kafka topic yet. Prepare the
                  configured capture topics and retry validation.
                </span>
                <Button
                  disabled={pending}
                  onClick={() => prepareTopics.mutate()}
                >
                  {prepareTopics.isPending
                    ? "Preparing topics…"
                    : "Prepare capture topics"}
                </Button>
              </div>
            )}
          <div className="wizard-actions">
            <Button
              variant="outline"
              disabled={
                pending ||
                step === 0 ||
                (!!destinationId && step === 2 && !selectedDestinationId)
              }
              onClick={() => {
                setStep((value) =>
                  value === 2 && selectedDestinationId ? 0 : value - 1,
                );
                review.reset();
                deploy.reset();
              }}
            >
              Back
            </Button>
            {step === 5 ? (
              <Button disabled={pending} onClick={() => deploy.mutate()}>
                <Rocket size={15} />
                {deploy.isPending ? "Deploying…" : "Deploy destination"}
              </Button>
            ) : (
              <Button
                disabled={
                  pending || (step === 2 && (!pipelineId || !mappings.length))
                }
                onClick={next}
              >
                {step === 4
                  ? "Validate and review"
                  : step === 1 && !tested
                    ? "Test connection to continue"
                    : "Continue"}
                <ArrowRight size={14} />
              </Button>
            )}
          </div>
        </section>
        <aside className="panel wizard-summary">
          <h3>Delivery summary</h3>
          <div>
            <small>Destination</small>
            <strong>{chosenName || "Choose a target"}</strong>
          </div>
          <div>
            <small>Capture pipeline</small>
            <strong>{pipeline.data?.name || "Select pipeline"}</strong>
          </div>
          <div>
            <small>Topics</small>
            <strong>{mappings.length} selected</strong>
          </div>
          <div>
            <small>Write mode</small>
            <strong>{options.write_mode}</strong>
          </div>
          <p className="summary-note">
            Capture keeps running independently. Each delivery has its own
            connector, offsets, and lifecycle. Add more destinations to fan out
            the same stream.
          </p>
        </aside>
      </div>
    </>
  );
}

export function DestinationDetailPage({ id }: { id: string }) {
  const router = useRouter();
  const params = useSearchParams();
  const client = useQueryClient();
  const [tab, setTab] = useState("Overview");
  const [editing, setEditing] = useState(params.get("edit") === "true");
  const [deleting, setDeleting] = useState(false);
  const [removeLink, setRemoveLink] = useState<Delivery | null>(null);
  const [mappingLink, setMappingLink] = useState<Delivery | null>(null);
  const [editMappings, setEditMappings] = useState<TopicMapping[]>([]);
  const query = useQuery({
    queryKey: ["destination", id],
    queryFn: () => api<Destination>(`/destinations/${id}`),
    refetchInterval: 10000,
  });
  const runtime = useQuery({
    queryKey: ["destination-status", id],
    queryFn: () => api<DestinationRuntime>(`/destinations/${id}/status`),
    refetchInterval: 10000,
  });
  const activity = useQuery({
    queryKey: ["audit", id],
    queryFn: () =>
      api<Audit[]>(
        `/audit?resource_id=${id}&include_related=true&meaningful=true`,
      ),
    enabled: tab === "Activity",
  });
  const form = useForm<DestinationForm>({
    resolver: zodResolver(destinationSchema),
    defaultValues: initialConnection,
  });
  const initializedEdit = useRef(false);
  useEffect(() => {
    if (
      params.get("edit") === "true" &&
      query.data &&
      !initializedEdit.current
    ) {
      form.reset({ ...initialConnection, ...query.data, password: "" });
      initializedEdit.current = true;
    }
  }, [params, query.data, form]);
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["destination"] });
    client.invalidateQueries({ queryKey: ["destination-status"] });
    client.invalidateQueries({ queryKey: ["destinations"] });
    client.invalidateQueries({ queryKey: ["audit", id] });
  };
  const operation = useMutation({
    mutationFn: (op: string) =>
      op === "delete"
        ? api(`/destinations/${id}`, { method: "DELETE" })
        : post(`/destinations/${id}/${op}`),
    onSuccess: (_, op) => {
      refresh();
      if (op === "delete") router.push("/destinations");
      else toast.success(`Destination ${op} request completed`);
    },
    onError: (error) => toast.error(error.message),
  });
  const save = useMutation({
    mutationFn: () =>
      api(`/destinations/${id}`, {
        method: "PUT",
        body: JSON.stringify({
          ...form.getValues(),
          password: form.getValues("password") || undefined,
        }),
      }),
    onSuccess: () => {
      refresh();
      setEditing(false);
      form.setValue("password", "");
    },
  });
  const remove = useMutation({
    mutationFn: () =>
      api(`/destinations/${id}/deliveries/${removeLink?.id}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      refresh();
      setRemoveLink(null);
      client.invalidateQueries({ queryKey: ["pipeline"] });
    },
  });
  const mapping = useMutation({
    mutationFn: () =>
      api(`/destinations/${id}/deliveries/${mappingLink?.id}/mappings`, {
        method: "PUT",
        body: JSON.stringify({
          ...mappingLink?.configuration_json,
          mappings: editMappings,
        }),
      }),
    onSuccess: () => {
      refresh();
      setMappingLink(null);
      toast.success("Mappings updated and runtime configuration applied");
    },
  });
  const deliveryOp = useMutation({
    mutationFn: ({
      delivery,
      op,
      task,
    }: {
      delivery: Delivery;
      op: string;
      task?: number;
    }) =>
      post(
        task !== undefined
          ? `/destinations/${id}/deliveries/${delivery.id}/tasks/${task}/restart`
          : `/destinations/${id}/${op}?delivery_id=${delivery.id}`,
      ),
    onSuccess: () => {
      refresh();
      toast.success("Delivery operation requested");
    },
    onError: (error) => toast.error(error.message),
  });
  if (query.isPending) return <Loading />;
  if (query.isError)
    return <ErrorPanel error={query.error} retry={() => query.refetch()} />;
  const target = query.data;
  const links = target.deliveries;
  const edit = () => {
    form.reset({ ...initialConnection, ...target, password: "" });
    setEditing(true);
  };
  return (
    <>
      <Link className="back-link" href="/destinations">
        <ArrowLeft size={14} />
        All destinations
      </Link>
      <PageHeader
        title={target.name}
        description={`${databaseName(target.type)} · ${target.environment} · ${target.host}:${target.port}/${target.database_name}`}
        eyebrow="COMPONENTS / DESTINATION"
      >
        <Status value={target.status} />
        <Button variant="outline" onClick={edit}>
          Edit
        </Button>
        <Button
          variant="outline"
          disabled={operation.isPending}
          onClick={() => operation.mutate("test")}
        >
          Test connection
        </Button>
        <Button
          variant="ghost"
          aria-label="Delete destination"
          onClick={() => setDeleting(true)}
        >
          <Trash2 size={16} />
        </Button>
      </PageHeader>
      <Tabs
        tabs={["Overview", "Used by Deliveries", "Configuration", "Activity"]}
        active={tab}
        onChange={setTab}
      />
      {runtime.isError && <ErrorPanel error={runtime.error} />}
      {tab === "Overview" && (
        <>
          <div className="resource-summary">
            <div className="metric-card">
              <span>Connection status</span>
              <strong>
                <Status value={target.status} />
              </strong>
              <small>Last checked: {date(target.last_health_check_at)}</small>
            </div>
            <div className="metric-card">
              <span>Database</span>
              <strong>{target.database_name}</strong>
              <small>
                {target.host}:{target.port}
              </small>
            </div>
            <div className="metric-card">
              <span>Environment</span>
              <strong>{target.environment}</strong>
              <small>{databaseName(target.type)} endpoint</small>
            </div>
            <div className="metric-card">
              <span>Used by deliveries</span>
              <strong>{links.length}</strong>
              <small>{target.connected_pipelines} pipelines</small>
            </div>
          </div>
          <div className="infrastructure-strip">
            <span>
              Host:{" "}
              <strong>
                {target.host}:{target.port}
              </strong>
            </span>
            <span>
              Database: <strong>{target.database_name}</strong>
            </span>
            <span>Connection checked: {date(target.last_health_check_at)}</span>
          </div>
        </>
      )}
      {(tab === "Overview" || tab === "Used by Deliveries") && (
        <>
          <section className="section">
            <div className="section-heading">
              <div>
                <h2>Used by Deliveries</h2>
                <p className="muted">
                  Delivery runtimes are managed independently from this
                  endpoint.
                </p>
              </div>
              <Button asChild variant="outline">
                <Link href={`/deliveries/new?destination_id=${id}`}>
                  <Plus size={14} />
                  Add delivery
                </Link>
              </Button>
            </div>
            {links.length ? (
              <div className="delivery-cards">
                {links.map((link) => (
                  <Link
                    className="panel delivery-card"
                    key={link.id}
                    href={`/deliveries/${link.id}`}
                  >
                    <div>
                      <strong>{link.pipeline_name}</strong>
                      <small>
                        {link.name} · {link.topic_mapping_json.length} topics ·{" "}
                        {link.delivery_mode}
                      </small>
                    </div>
                    <Status
                      value={
                        runtime.data?.deliveries.find(
                          (value) => value.delivery_id === link.id,
                        )?.actual_state || link.actual_state
                      }
                    />
                  </Link>
                ))}
              </div>
            ) : (
              <Empty
                title="No delivery configured"
                description="Choose a capture pipeline and topics to start delivering records to this target."
                href={`/deliveries/new?destination_id=${id}`}
                action="Add delivery"
              />
            )}
          </section>
        </>
      )}
      {tab === "Mappings" && (
        <>
          {links.map((link) => (
            <section key={link.id} className="section">
              <div className="section-heading">
                <h2>
                  {link.pipeline_name} · {link.name}
                </h2>
                <Button
                  variant="outline"
                  onClick={() => {
                    setMappingLink(link);
                    setEditMappings(link.topic_mapping_json);
                    mapping.reset();
                  }}
                >
                  Edit mappings
                </Button>
              </div>
              <DataTable
                data={link.topic_mapping_json}
                columns={[
                  { accessorKey: "topic", header: "Kafka topic" },
                  {
                    id: "target",
                    header: "Destination table",
                    cell: ({ row }) =>
                      `${row.original.schema_name}.${row.original.table_name}`,
                  },
                  {
                    id: "key",
                    header: "Key strategy",
                    cell: () => "Record key",
                  },
                  {
                    id: "write",
                    header: "Write mode",
                    cell: () => link.delivery_mode,
                  },
                  {
                    id: "evolve",
                    header: "Evolution",
                    cell: () =>
                      link.configuration_json.auto_evolve
                        ? "Add columns"
                        : "Off",
                  },
                  {
                    id: "state",
                    header: "Delivery state",
                    cell: () => <Status value={link.actual_state} />,
                  },
                ]}
              />
            </section>
          ))}
          {!links.length && (
            <Empty
              title="No mappings yet"
              description={`Add a delivery to map Kafka topics to ${databaseName(target.type)} tables.`}
            />
          )}
        </>
      )}
      {tab === "Delivery" && (
        <>
          <div className="info-strip">
            Kafka Connect writes directly to {databaseName(target.type)}.
            Runtime RUNNING does not establish successful writes or delivery
            lag; those metrics remain unavailable.
          </div>
          {links.map((link) => (
            <div className="panel delivery-settings" key={link.id}>
              <div className="section-heading">
                <h2>{link.name}</h2>
                <Status value={link.actual_state} />
              </div>
              <dl className="definition-grid">
                <div>
                  <dt>Desired state</dt>
                  <dd>{link.desired_state}</dd>
                </div>
                <div>
                  <dt>Write mode</dt>
                  <dd>{link.delivery_mode}</dd>
                </div>
                <div>
                  <dt>Auto create / evolve</dt>
                  <dd>
                    {link.configuration_json.auto_create ? "On" : "Off"} /{" "}
                    {link.configuration_json.auto_evolve ? "On" : "Off"}
                  </dd>
                </div>
                <div>
                  <dt>Delete handling</dt>
                  <dd>
                    {link.configuration_json.delete_enabled
                      ? "Delete rows"
                      : "Ignore"}
                  </dd>
                </div>
                <div>
                  <dt>Delivery lag</dt>
                  <dd>
                    <Unavailable />
                  </dd>
                </div>
                <div>
                  <dt>Last successful write</dt>
                  <dd>
                    <Unavailable />
                  </dd>
                </div>
              </dl>
              <div className="header-actions">
                <Button
                  variant="outline"
                  disabled={deliveryOp.isPending}
                  onClick={() =>
                    deliveryOp.mutate({ delivery: link, op: "pause" })
                  }
                >
                  Pause
                </Button>
                <Button
                  variant="outline"
                  disabled={deliveryOp.isPending}
                  onClick={() =>
                    deliveryOp.mutate({ delivery: link, op: "resume" })
                  }
                >
                  Resume
                </Button>
                <Button
                  variant="outline"
                  disabled={deliveryOp.isPending}
                  onClick={() =>
                    deliveryOp.mutate({ delivery: link, op: "restart" })
                  }
                >
                  Restart
                </Button>
                <Button variant="ghost" onClick={() => setRemoveLink(link)}>
                  Remove delivery
                </Button>
              </div>
            </div>
          ))}
        </>
      )}
      {tab === "Connector" && (
        <>
          {links.map((link) => {
            const state = runtime.data?.deliveries.find(
              (value) => value.delivery_id === link.id,
            );
            return (
              <section className="panel delivery-settings" key={link.id}>
                <div className="section-heading">
                  <div>
                    <h2>{link.connector?.name}</h2>
                    <small className="muted">
                      {link.connector?.connector_class}
                    </small>
                  </div>
                  <Status value={state?.actual_state || link.actual_state} />
                </div>
                {state?.error && (
                  <div className="error-panel" role="alert">
                    {state.error.message}
                  </div>
                )}
                <p className="muted">
                  Worker: {state?.connector?.worker_id || "Unavailable"}
                </p>
                <DataTable
                  search={false}
                  data={state?.tasks || []}
                  columns={[
                    { accessorKey: "id", header: "Task" },
                    {
                      accessorKey: "state",
                      header: "State",
                      cell: ({ row }) => <Status value={row.original.state} />,
                    },
                    { accessorKey: "worker_id", header: "Worker" },
                    { accessorKey: "error", header: "Error" },
                    {
                      id: "restart",
                      header: "Action",
                      cell: ({ row }) => (
                        <Button
                          variant="outline"
                          disabled={deliveryOp.isPending}
                          onClick={() =>
                            deliveryOp.mutate({
                              delivery: link,
                              op: "restart",
                              task: row.original.id,
                            })
                          }
                        >
                          Restart task
                        </Button>
                      ),
                    },
                  ]}
                />
                <div className="header-actions">
                  <Button
                    variant="outline"
                    disabled={deliveryOp.isPending}
                    onClick={() =>
                      deliveryOp.mutate({ delivery: link, op: "restart" })
                    }
                  >
                    Restart connector
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => setTab("Configuration")}
                  >
                    View raw config
                  </Button>
                </div>
              </section>
            );
          })}
          {!links.length && (
            <Empty
              title="No sink connector"
              description="Deploy a delivery to inspect its underlying runtime here."
            />
          )}
        </>
      )}
      {tab === "Configuration" && (
        <section className="panel">
          <div className="section-heading">
            <div>
              <h2>Connection configuration</h2>
              <p className="muted">
                Endpoint settings only. Sink connector configuration lives on
                each Delivery.
              </p>
            </div>
          </div>
          <dl className="facts">
            <dt>Database type</dt>
            <dd>{databaseName(target.type)}</dd>
            <dt>Host</dt>
            <dd>{target.host}</dd>
            <dt>Port</dt>
            <dd>{target.port}</dd>
            <dt>Database</dt>
            <dd>{target.database_name}</dd>
            <dt>Username</dt>
            <dd>{target.username}</dd>
            <dt>TLS / SSL</dt>
            <dd>{target.ssl_enabled ? "Enabled" : "Disabled"}</dd>
          </dl>
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
              { accessorKey: "action", header: "Action" },
              { accessorKey: "actor", header: "Actor" },
            ]}
          />
        ))}
      <Dialog
        open={editing}
        onOpenChange={setEditing}
        title="Edit destination"
        description="Connection settings cannot change while deliveries exist. Name, environment, and description can be updated."
      >
        <form onSubmit={form.handleSubmit(() => save.mutate())}>
          <ConnectionFields form={form} existing />
          <div className="dialog-actions">
            <Button
              variant="outline"
              type="button"
              onClick={() => setEditing(false)}
            >
              Cancel
            </Button>
            <Button disabled={save.isPending}>Save destination</Button>
          </div>
          {save.isError && <ErrorPanel error={save.error} />}
        </form>
      </Dialog>
      <Dialog
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete destination?"
        description="Remove deliveries first. Target data is retained."
      >
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setDeleting(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={operation.isPending}
            onClick={() => operation.mutate("delete")}
          >
            Delete destination
          </Button>
        </div>
        {operation.isError && <ErrorPanel error={operation.error} />}
      </Dialog>
      <Dialog
        open={!!removeLink}
        onOpenChange={(open) => {
          if (!open) setRemoveLink(null);
        }}
        title="Remove delivery?"
        description="The sink connector and association are deleted. Capture, Kafka topics, and destination rows are retained."
      >
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setRemoveLink(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Remove delivery
          </Button>
        </div>
        {remove.isError && <ErrorPanel error={remove.error} />}
      </Dialog>
      <Dialog
        open={!!mappingLink}
        onOpenChange={(open) => {
          if (!open) setMappingLink(null);
        }}
        title="Edit delivery mappings"
        description="Validates target readiness, refreshes discovered types, and updates the live sink. Previous target rows are retained."
      >
        {editMappings.map((value, index) => (
          <div className="form-grid" key={value.topic}>
            <p className="full-width">{value.topic}</p>
            <Field label={`Schema for ${value.topic}`}>
              <Input
                value={value.schema_name}
                onChange={(event) =>
                  setEditMappings((values) =>
                    values.map((entry, i) =>
                      i === index
                        ? { ...entry, schema_name: event.target.value }
                        : entry,
                    ),
                  )
                }
              />
            </Field>
            <Field label={`Table for ${value.topic}`}>
              <Input
                value={value.table_name}
                onChange={(event) =>
                  setEditMappings((values) =>
                    values.map((entry, i) =>
                      i === index
                        ? { ...entry, table_name: event.target.value }
                        : entry,
                    ),
                  )
                }
              />
            </Field>
          </div>
        ))}
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setMappingLink(null)}>
            Cancel
          </Button>
          <Button disabled={mapping.isPending} onClick={() => mapping.mutate()}>
            Apply mappings
          </Button>
        </div>
        {mapping.isError && <ErrorPanel error={mapping.error} />}
      </Dialog>
    </>
  );
}
