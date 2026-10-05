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
import type {
  ConnectCluster,
  DatabaseProviderMetadata,
  Delivery,
  Destination,
  Pipeline,
  PipelineDetail,
  TopicMapping,
} from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, Database, Rocket } from "lucide-react";
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

export const steps = [
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
    queryFn: async () =>
      (await api<Destination[]>("/destinations")).filter(
        (item) => item.type === "postgresql" || item.type === "mysql",
      ),
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
