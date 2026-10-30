"use client";

import {
  DataTable,
  Empty,
  ErrorPanel,
  Field,
  JsonView,
  Loading,
  PageHeader,
  Status,
} from "@/components/common";
import { api, ApiError, post } from "@/lib/api";
import { destinationSchema, type DestinationForm } from "@/lib/validation";
import {
  connectionTestPassed,
  type ConnectionTestFeedback,
} from "@/lib/connection-test";
import type {
  Connection,
  ConnectCluster,
  DatabaseProviderMetadata,
  Delivery,
  Destination,
  Pipeline,
  PipelineDetail,
  PipelineTable,
  TopicMapping,
} from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowLeft,
  ArrowRight,
  Check,
  Database,
  Rocket,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import {
  ConnectionFields,
  databaseName,
  initialConnection,
  initialDelivery,
} from "./shared";
import { useAuthorization } from "@/lib/auth";

export const steps = [
  "Destination",
  "Connection",
  "Delivery source",
  "Mapping",
  "Options",
  "Review",
];

type DeliveryType = "DATABASE" | "OBJECT_STORAGE";
type DeliveryDestination = Omit<Destination, "type"> & {
  type:
    | Destination["type"]
    | "clickhouse"
    | "bigquery"
    | "redshift"
    | "azure_synapse";
};

const defaultObjectKeyTemplate =
  "{{topic}}/year={{timestamp:unit=yyyy}}/month={{timestamp:unit=MM}}/day={{timestamp:unit=dd}}/{{topic}}-{{partition:padding=true}}-{{start_offset:padding=true}}.jsonl.gz";

function databaseDeliveryReadiness(table: PipelineTable) {
  const syncMode = table.sync_mode || "CURRENT_STATE";
  const keyMode =
    table.effective_key_mode ||
    (table.primary_key_columns.length ? "PRIMARY_KEY" : "NONE");
  const keyColumns = table.effective_key_columns ?? table.primary_key_columns;
  const databaseEnforced =
    keyMode === "PRIMARY_KEY" ||
    table.effective_key_enforcement === "DATABASE_ENFORCED";
  const ready =
    syncMode === "CURRENT_STATE" && keyColumns.length > 0 && databaseEnforced;

  if (ready) {
    const label =
      keyMode === "PRIMARY_KEY"
        ? "Primary key"
        : keyMode === "UNIQUE_INDEX"
          ? keyColumns.length > 1
            ? "Composite unique key"
            : "Unique key"
          : "Effective key";
    return { ready, message: `${label}: ${keyColumns.join(", ")}` };
  }
  if (syncMode !== "CURRENT_STATE") {
    return {
      ready,
      message: `Unavailable for database Current State: ${syncMode.replaceAll("_", " ")}`,
    };
  }
  if (keyColumns.length && !databaseEnforced) {
    return {
      ready,
      message: "Unavailable: effective key is not database-enforced",
    };
  }
  return {
    ready,
    message: "Unavailable: no database-enforced effective key",
  };
}

export function DestinationWizard({
  deliveryFirst = false,
  initialDeliveryType,
}: {
  deliveryFirst?: boolean;
  initialDeliveryType?: DeliveryType;
} = {}) {
  const { can } = useAuthorization();
  const canCreateDestination = can("destinations.write");
  const router = useRouter();
  const params = useSearchParams();
  const client = useQueryClient();
  const requestedType = params.get("type")?.toUpperCase();
  const [deliveryType, setDeliveryType] = useState<DeliveryType>(
    initialDeliveryType ||
      (requestedType === "OBJECT_STORAGE" ? "OBJECT_STORAGE" : "DATABASE"),
  );
  const providers = useQuery({
    queryKey: ["database-providers"],
    queryFn: () => api<DatabaseProviderMetadata[]>("/database-providers"),
    enabled: canCreateDestination,
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
  const [topics, setTopics] = useState<string[]>([]);
  const [automatic, setAutomatic] = useState(true);
  const [options, setOptions] = useState(initialDelivery);
  const [storageOptions, setStorageOptions] = useState({
    name: "Object storage archive",
    compression: "gzip" as "gzip" | "none",
    file_max_records: 100000,
    flush_interval_ms: 60000,
    tasks_max: 1,
    error_policy: "fail" as "fail" | "continue",
    file_name_template: defaultObjectKeyTemplate,
  });
  const [preview, setPreview] = useState<{
    config: Record<string, string>;
  } | null>(null);
  const [testedFingerprint, setTestedFingerprint] = useState<string | null>(
    null,
  );
  const connection = useForm<DestinationForm>({
    resolver: zodResolver(destinationSchema),
    defaultValues: initialConnection,
  });
  const selectedDestinationType = useWatch({
    control: connection.control,
    name: "type",
  });
  const connectionFingerprint = JSON.stringify(
    useWatch({ control: connection.control }),
  );
  const tested = testedFingerprint === connectionFingerprint;
  const existing = useQuery({
    queryKey: ["destination", destinationId],
    queryFn: () => api<DeliveryDestination>(`/destinations/${destinationId}`),
    enabled: !!destinationId && deliveryType === "DATABASE",
  });
  const registeredDestinations = useQuery({
    queryKey: ["destinations"],
    queryFn: async () =>
      (await api<DeliveryDestination[]>("/destinations")).filter(
        (item) =>
          item.type === "postgresql" ||
          item.type === "mysql" ||
          item.type === "clickhouse" ||
          item.type === "bigquery" ||
          item.type === "redshift" ||
          item.type === "azure_synapse",
      ),
    enabled: step === 0,
  });
  const storageDestinations = useQuery({
    queryKey: ["connections", "object-storage-destinations"],
    queryFn: () =>
      api<Connection[]>(
        "/connections?category=OBJECT_STORAGE&capability=DESTINATION",
      ),
    enabled: step === 0 || deliveryType === "OBJECT_STORAGE",
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
  const payload = () =>
    deliveryType === "OBJECT_STORAGE"
      ? {
          pipeline_id: pipelineId,
          name: storageOptions.name,
          delivery_type: "OBJECT_STORAGE" as const,
          topics,
          output_format: "JSONL" as const,
          compression: storageOptions.compression,
          file_max_records: storageOptions.file_max_records,
          flush_interval_ms: storageOptions.flush_interval_ms,
          tasks_max: storageOptions.tasks_max,
          error_policy: storageOptions.error_policy,
          file_name_template:
            storageOptions.compression === "none"
              ? storageOptions.file_name_template.replace(/\.gz$/, "")
              : storageOptions.file_name_template,
        }
      : {
          ...options,
          pipeline_id: pipelineId,
          delivery_type: "DATABASE" as const,
          mappings,
        };
  const test = useMutation({
    mutationFn: async () => {
      const values = connection.getValues();
      const fingerprint = JSON.stringify(values);
      const result = await post<ConnectionTestFeedback>(
        "/destinations/test-connection",
        values,
      );
      if (!connectionTestPassed(result))
        throw new Error("Destination connection checks failed");
      return fingerprint;
    },
    onSuccess: (fingerprint) => {
      setTestedFingerprint(fingerprint);
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
    if (step === 0) {
      if (!canCreateDestination) return;
      setDeliveryType("DATABASE");
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
    if (
      step === 2 &&
      (!pipelineId ||
        (deliveryType === "OBJECT_STORAGE" ? !topics.length : !mappings.length))
    )
      return;
    if (step === 4) {
      review.mutate();
      return;
    }
    setStep((value) => value + 1);
  };
  const chosenName =
    (deliveryType === "OBJECT_STORAGE"
      ? storageDestinations.data?.find(
          (target) => target.id === selectedDestinationId,
        )?.name
      : registeredDestinations.data?.find(
          (target) => target.id === selectedDestinationId,
        )?.name) ||
    existing.data?.name ||
    connection.getValues("name");
  const chosenDestination =
    deliveryType === "DATABASE"
      ? registeredDestinations.data?.find(
          (target) => target.id === selectedDestinationId,
        ) || existing.data
      : undefined;
  const targetNamespace =
    chosenDestination?.type === "azure_synapse"
      ? String(chosenDestination.provider_options.target_schema || "dbo")
      : ["mysql", "clickhouse", "bigquery", "redshift"].includes(
            chosenDestination?.type || connection.getValues("type"),
          )
        ? chosenDestination?.database_name ||
          connection.getValues("database_name")
        : "public";
  const appendOnlyHistory = [
    "clickhouse",
    "bigquery",
    "redshift",
    "azure_synapse",
  ].includes(chosenDestination?.type || "");
  const selectedTopicCount =
    deliveryType === "OBJECT_STORAGE" ? topics.length : mappings.length;
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
                Choose a registered database, S3, or MinIO destination for this
                delivery.
                {canCreateDestination &&
                  " You can also add a new database destination."}
              </p>
              <Field
                label="Destination"
                hint="Existing destinations reuse their saved connection. Choose topics and mappings in the next steps."
              >
                <select
                  value={selectedDestinationId}
                  onChange={(event) => {
                    const selected = event.target.selectedOptions[0];
                    const destination = registeredDestinations.data?.find(
                      (target) => target.id === event.target.value,
                    );
                    setSelectedDestinationId(event.target.value);
                    setDeliveryType(
                      selected?.dataset.deliveryType === "OBJECT_STORAGE"
                        ? "OBJECT_STORAGE"
                        : "DATABASE",
                    );
                    setDestinationId("");
                    setMappings([]);
                    setTopics([]);
                    setPreview(null);
                    if (
                      destination?.type === "clickhouse" ||
                      destination?.type === "bigquery" ||
                      destination?.type === "redshift" ||
                      destination?.type === "azure_synapse"
                    ) {
                      setOptions((value) => ({
                        ...value,
                        write_mode: "insert",
                        auto_evolve: false,
                        delete_enabled: false,
                      }));
                    }
                  }}
                >
                  <option value="">
                    {canCreateDestination
                      ? "Add a new database destination"
                      : "Select a destination"}
                  </option>
                  {!!registeredDestinations.data?.length && (
                    <optgroup label="Database">
                      {registeredDestinations.data
                        .slice()
                        .sort((a, b) => a.name.localeCompare(b.name))
                        .map((target) => (
                          <option
                            key={target.id}
                            value={target.id}
                            data-delivery-type="DATABASE"
                          >
                            {target.name} · {databaseName(target.type)} ·{" "}
                            {target.environment}
                          </option>
                        ))}
                    </optgroup>
                  )}
                  {!!storageDestinations.data?.length && (
                    <optgroup label="S3 / MinIO">
                      {storageDestinations.data
                        .slice()
                        .sort((a, b) => a.name.localeCompare(b.name))
                        .map((target) => (
                          <option
                            key={target.id}
                            value={target.id}
                            data-delivery-type="OBJECT_STORAGE"
                          >
                            {target.name} · {target.provider}
                          </option>
                        ))}
                    </optgroup>
                  )}
                </select>
              </Field>
              {(registeredDestinations.isPending ||
                storageDestinations.isPending) && (
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
              {storageDestinations.isError && (
                <ErrorPanel
                  error={storageDestinations.error}
                  retry={() => storageDestinations.refetch()}
                />
              )}
              {registeredDestinations.isSuccess &&
                storageDestinations.isSuccess &&
                !registeredDestinations.data.length &&
                !storageDestinations.data.length && (
                  <p className="muted">
                    {canCreateDestination
                      ? "No destinations registered yet. Add your first destination below."
                      : "No destinations are available. Ask an Admin to add one."}
                  </p>
                )}
              {selectedDestinationId ? (
                <div className="info-strip">
                  {deliveryType === "OBJECT_STORAGE" ? (
                    <Archive size={17} />
                  ) : (
                    <Database size={17} />
                  )}
                  <span>
                    The saved{" "}
                    {deliveryType === "OBJECT_STORAGE"
                      ? "S3 / MinIO"
                      : "database"}{" "}
                    connection will be reused. No new destination will be
                    created.
                  </span>
                </div>
              ) : canCreateDestination ? (
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
              ) : null}
            </>
          )}
          {step === 1 && (
            <>
              <div onChange={() => setTestedFingerprint(null)}>
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
                  {test.isPending ? "Testing…" : "Test connection"}
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
                    setTopics([]);
                    setPreview(null);
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
                    {pipeline.data.tables.map((table) => {
                      const readiness = databaseDeliveryReadiness(table);
                      const storage = deliveryType === "OBJECT_STORAGE";
                      const selectable =
                        storage || appendOnlyHistory || readiness.ready;
                      return (
                        <label key={table.topic_name}>
                          <input
                            type="checkbox"
                            disabled={!selectable}
                            checked={
                              storage
                                ? topics.includes(table.topic_name)
                                : mappings.some(
                                    (mapping) =>
                                      mapping.topic === table.topic_name,
                                  )
                            }
                            onChange={(event) => {
                              setPreview(null);
                              if (storage) {
                                setTopics((values) =>
                                  event.target.checked
                                    ? [...values, table.topic_name]
                                    : values.filter(
                                        (topic) => topic !== table.topic_name,
                                      ),
                                );
                                return;
                              }
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
                              );
                            }}
                          />
                          <span>
                            <strong>{table.topic_name}</strong>
                            <small>
                              {table.schema_name}.{table.table_name} ·{" "}
                              {storage
                                ? "CDC event stream · source key optional"
                                : appendOnlyHistory
                                  ? "Append-only CDC history; source key optional"
                                  : readiness.message}
                            </small>
                          </span>
                        </label>
                      );
                    })}
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
          {step === 3 &&
            (deliveryType === "OBJECT_STORAGE" ? (
              <>
                <div className="info-strip">
                  <Archive size={17} />
                  <span>
                    Object storage keeps the Debezium event envelope as JSONL.
                    Table mapping and primary keys are not required.
                  </span>
                </div>
                <div className="mapping-editor">
                  {topics.map((topic) => (
                    <div className="mapping-row" key={topic}>
                      <strong>{topic}</strong>
                      <ArrowRight size={16} />
                      <span>S3 / MinIO JSONL objects</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
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
                                  ? {
                                      ...value,
                                      schema_name: event.target.value,
                                    }
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
            ))}
          {step === 4 &&
            (deliveryType === "OBJECT_STORAGE" ? (
              <>
                <div className="form-grid">
                  <Field label="Delivery name">
                    <Input
                      value={storageOptions.name}
                      onChange={(event) =>
                        setStorageOptions((value) => ({
                          ...value,
                          name: event.target.value,
                        }))
                      }
                    />
                  </Field>
                  <Field label="Compression">
                    <select
                      value={storageOptions.compression}
                      onChange={(event) =>
                        setStorageOptions((value) => ({
                          ...value,
                          compression: event.target.value as "gzip" | "none",
                        }))
                      }
                    >
                      <option value="gzip">gzip</option>
                      <option value="none">None</option>
                    </select>
                  </Field>
                  <Field label="Maximum records per file">
                    <Input
                      type="number"
                      min={1}
                      value={storageOptions.file_max_records}
                      onChange={(event) =>
                        setStorageOptions((value) => ({
                          ...value,
                          file_max_records: Number(event.target.value),
                        }))
                      }
                    />
                  </Field>
                  <Field label="Flush interval (milliseconds)">
                    <Input
                      type="number"
                      min={1000}
                      value={storageOptions.flush_interval_ms}
                      onChange={(event) =>
                        setStorageOptions((value) => ({
                          ...value,
                          flush_interval_ms: Number(event.target.value),
                        }))
                      }
                    />
                  </Field>
                  <Field label="Maximum tasks">
                    <Input
                      type="number"
                      min={1}
                      max={4}
                      value={storageOptions.tasks_max}
                      onChange={(event) =>
                        setStorageOptions((value) => ({
                          ...value,
                          tasks_max: Number(event.target.value),
                        }))
                      }
                    />
                  </Field>
                  <Field label="Error policy">
                    <select
                      value={storageOptions.error_policy}
                      onChange={(event) =>
                        setStorageOptions((value) => ({
                          ...value,
                          error_policy: event.target.value as
                            "fail" | "continue",
                        }))
                      }
                    >
                      <option value="fail">Stop on error</option>
                      <option value="continue">
                        Continue past bad records
                      </option>
                    </select>
                  </Field>
                  <div className="full-width">
                    <Field label="Object key template">
                      <Input
                        value={storageOptions.file_name_template}
                        onChange={(event) =>
                          setStorageOptions((value) => ({
                            ...value,
                            file_name_template: event.target.value,
                          }))
                        }
                      />
                    </Field>
                  </div>
                </div>
                <p className="muted">
                  JSONL preserves the Debezium event envelope, including
                  deletes. Files are grouped by topic, partition, and starting
                  offset; replaying data may duplicate event content.
                </p>
              </>
            ) : (
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
                      disabled={appendOnlyHistory}
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
                  {appendOnlyHistory && (
                    <p className="muted full-width">
                      This destination stores Debezium event history, including
                      update and delete envelopes. It does not represent only
                      the latest source row state.
                    </p>
                  )}
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
            ))}
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
                  <small>Delivery mode</small>
                  <strong>
                    {deliveryType === "OBJECT_STORAGE"
                      ? `JSONL · ${storageOptions.compression}`
                      : options.write_mode}
                  </strong>
                </div>
                <div>
                  <small>Topics</small>
                  <strong>{selectedTopicCount}</strong>
                </div>
              </div>
              <h3>
                {deliveryType === "OBJECT_STORAGE" ? "Topics" : "Mappings"}
              </h3>
              {deliveryType === "OBJECT_STORAGE" ? (
                <DataTable
                  search={false}
                  data={topics.map((topic) => ({ topic }))}
                  columns={[
                    { accessorKey: "topic", header: "Kafka topic" },
                    {
                      id: "output",
                      header: "Destination format",
                      cell: () => "S3 / MinIO JSONL objects",
                    },
                  ]}
                />
              ) : (
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
              )}
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
                  pending ||
                  (step === 0 &&
                    !canCreateDestination &&
                    !selectedDestinationId) ||
                  (step === 2 && (!pipelineId || !selectedTopicCount))
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
            <strong>{selectedTopicCount} selected</strong>
          </div>
          <div>
            <small>Delivery mode</small>
            <strong>
              {deliveryType === "OBJECT_STORAGE"
                ? `JSONL · ${storageOptions.compression}`
                : options.write_mode}
            </strong>
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
