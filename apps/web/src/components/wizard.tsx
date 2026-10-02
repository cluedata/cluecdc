"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Database,
  GitBranch,
  Loader2,
  Plus,
} from "lucide-react";
import type {
  ConnectCluster,
  Destination,
  KafkaCluster,
  Source,
  SourceTable,
  TopicMapping,
} from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { api, number, post } from "@/lib/api";
import { captureSchema, type CaptureForm } from "@/lib/validation";
import {
  createPipelineFlow,
  PipelineCreationError,
  retryPipelineDelivery,
  type DeliveryCreateInput,
} from "@/services/pipeline-orchestrator";
import {
  Empty,
  ErrorPanel,
  Field,
  JsonView,
  Loading,
  PageHeader,
  Status,
} from "./common";
import { DataFlowRail } from "./data-flow-rail";

const steps = ["Source", "Capture", "Stream", "Delivery", "Review"];
type DeliveryDraft = Omit<DeliveryCreateInput, "pipeline_id" | "mappings">;
const deliveryDefaults: DeliveryDraft = {
  name: "Primary delivery",
  write_mode: "upsert" as const,
  primary_key_mode: "record_key" as const,
  auto_create: true,
  auto_evolve: true,
  delete_enabled: true,
  null_handling: "ignore" as const,
  batch_size: 500,
  max_retries: 10,
  retry_backoff_ms: 3000,
  tasks_max: 1,
};

export function PipelineWizard() {
  const router = useRouter();
  const params = useSearchParams();
  const [step, setStep] = useState(0);
  const [sourceId, setSourceId] = useState(params.get("source") || "");
  const [selected, setSelected] = useState<string[]>([]);
  const [tableSearch, setTableSearch] = useState("");
  const [clusterId, setClusterId] = useState("");
  const [connectId, setConnectId] = useState("");
  const [destinationId, setDestinationId] = useState(
    params.get("destination_id") || "",
  );
  const [delivery, setDelivery] = useState<DeliveryDraft>(deliveryDefaults);
  const [preview, setPreview] = useState<{
    config: Record<string, string>;
    topics: string[];
  } | null>(null);
  const [partial, setPartial] = useState<PipelineCreationError | null>(null);

  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => api<Source[]>("/sources"),
  });
  const tables = useQuery({
    queryKey: ["source-tables", sourceId],
    queryFn: () => api<SourceTable[]>(`/sources/${sourceId}/tables`),
    enabled: !!sourceId,
  });
  const kafka = useQuery({
    queryKey: ["kafka-clusters"],
    queryFn: () => api<KafkaCluster[]>("/kafka/clusters"),
  });
  const connect = useQuery({
    queryKey: ["connect-clusters"],
    queryFn: () => api<ConnectCluster[]>("/connect/clusters"),
  });
  const destinations = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api<Destination[]>("/destinations"),
  });
  const form = useForm<CaptureForm>({
    resolver: zodResolver(captureSchema),
    defaultValues: {
      name: "Commerce pipeline",
      topic_prefix: "commerce",
      snapshot_mode: "initial",
      heartbeat_interval_ms: 10000,
      max_batch_size: 2048,
      max_queue_size: 8192,
      poll_interval_ms: 500,
      additional_debezium_properties: {},
    },
  });
  const eligible = (tables.data || []).filter((table) => table.cdc_ready);
  const chosen = eligible.filter((table) =>
    selected.includes(`${table.schema_name}.${table.table_name}`),
  );
  const source = sources.data?.find((item) => item.id === sourceId);
  const stream = kafka.data?.find((item) => item.id === clusterId);
  const connectCluster = connect.data?.find((item) => item.id === connectId);
  const destination = destinations.data?.find(
    (item) => item.id === destinationId,
  );
  const destinationOptions = destinations.data || [];
  const topics = chosen.map(
    (table) =>
      `${form.getValues("topic_prefix")}.${table.schema_name}.${table.table_name}`,
  );
  const mappings: TopicMapping[] = chosen.map((table, index) => ({
    topic: topics[index],
    schema_name:
      destination?.type === "mysql"
        ? destination.database_name
        : table.schema_name,
    table_name: table.table_name,
  }));
  const capturePayload = () => ({
    ...form.getValues(),
    source_id: sourceId,
    kafka_cluster_id: clusterId,
    connect_cluster_id: connectId,
    tables: chosen.map((table) => ({
      schema_name: table.schema_name,
      table_name: table.table_name,
    })),
  });
  const deliveryPayload = (): Omit<DeliveryCreateInput, "pipeline_id"> => ({
    ...delivery,
    mappings,
  });

  const review = useMutation({
    mutationFn: () =>
      post<{ config: Record<string, string>; topics: string[] }>(
        "/pipelines/preview",
        capturePayload(),
      ),
    onSuccess: (value) => {
      setPreview(value);
      setStep(4);
    },
  });
  const create = useMutation({
    mutationFn: () =>
      createPipelineFlow(capturePayload(), destinationId, deliveryPayload()),
    onSuccess: ({ pipeline }) => router.push(`/pipelines/${pipeline.id}`),
    onError: (error) => {
      if (error instanceof PipelineCreationError) setPartial(error);
    },
  });
  const retry = useMutation({
    mutationFn: () =>
      retryPipelineDelivery(
        partial!.pipeline!.id,
        destinationId,
        deliveryPayload(),
      ),
    onSuccess: () => router.push(`/pipelines/${partial!.pipeline!.id}`),
  });
  const canContinue = (() => {
    if (step === 0) return !!sourceId;
    if (step === 1) return chosen.length > 0;
    if (step === 2) return !!clusterId && !!connectId;
    if (step === 3) return !!destinationId && !!delivery.name.trim();
    return true;
  })();
  const next = async () => {
    if (!canContinue) return;
    if (step === 1 && !(await form.trigger())) return;
    if (step === 3) return review.mutate();
    setStep((value) => Math.min(4, value + 1));
  };

  return (
    <>
      <Link className="back-link" href="/pipelines">
        <ArrowLeft size={14} /> All pipelines
      </Link>
      <PageHeader
        title="Create Pipeline"
        description="Configure the complete data path from a source database through Kafka to a destination."
        eyebrow="DATA FLOW"
      />
      <div className="wizard-steps" aria-label="Pipeline creation progress">
        {steps.map((label, index) => (
          <div
            key={label}
            className={index === step ? "current" : index < step ? "done" : ""}
            aria-current={index === step ? "step" : undefined}
          >
            <span>{index < step ? <Check size={15} /> : index + 1}</span>
            {label}
          </div>
        ))}
      </div>
      <div className="wizard-layout">
        <section className="panel wizard-panel">
          <span className="step-label">STEP {step + 1} OF 5</span>
          <h2>
            {
              [
                "Where does your data come from?",
                "What data do you want to capture?",
                "Where should captured events be streamed?",
                "Where should the data go?",
                "Review your pipeline",
              ][step]
            }
          </h2>

          {step === 0 &&
            (sources.isPending ? (
              <Loading />
            ) : sources.isError ? (
              <ErrorPanel
                error={sources.error}
                retry={() => sources.refetch()}
              />
            ) : !sources.data.length ? (
              <Empty
                title="No sources registered"
                description="Register a PostgreSQL or MySQL source before creating a pipeline."
                href="/sources/new"
                action="Create Source"
              />
            ) : (
              <>
                <div className="selection-list">
                  {sources.data.map((item) => (
                    <label
                      key={item.id}
                      className={`selection-card ${sourceId === item.id ? "selected" : ""}`}
                    >
                      <input
                        type="radio"
                        name="source"
                        checked={sourceId === item.id}
                        onChange={() => {
                          setSourceId(item.id);
                          setSelected([]);
                        }}
                      />
                      <Database size={22} />
                      <div>
                        <strong>{item.name}</strong>
                        <small>
                          {item.type} · {item.host}:{item.port}
                        </small>
                      </div>
                      <Status value={item.status} />
                    </label>
                  ))}
                </div>
                <Button asChild variant="outline">
                  <Link href="/sources/new">
                    <Plus size={14} /> Create New Source
                  </Link>
                </Button>
              </>
            ))}

          {step === 1 &&
            (tables.isPending ? (
              <Loading />
            ) : tables.isError ? (
              <ErrorPanel error={tables.error} retry={() => tables.refetch()} />
            ) : (
              <>
                <div className="filters">
                  <Input
                    aria-label="Search tables"
                    placeholder="Search schema or table"
                    value={tableSearch}
                    onChange={(event) => setTableSearch(event.target.value)}
                  />
                  <Button
                    variant="outline"
                    onClick={() =>
                      setSelected(
                        eligible.map(
                          (item) => `${item.schema_name}.${item.table_name}`,
                        ),
                      )
                    }
                  >
                    Select all
                  </Button>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Select</th>
                        <th>Table</th>
                        <th>Rows</th>
                        <th>Primary key</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(tables.data || [])
                        .filter((item) =>
                          `${item.schema_name}.${item.table_name}`
                            .toLowerCase()
                            .includes(tableSearch.toLowerCase()),
                        )
                        .map((item) => {
                          const key = `${item.schema_name}.${item.table_name}`;
                          return (
                            <tr key={item.id}>
                              <td>
                                <input
                                  type="checkbox"
                                  aria-label={`Capture ${key}`}
                                  disabled={!item.cdc_ready}
                                  checked={selected.includes(key)}
                                  onChange={(event) =>
                                    setSelected((old) =>
                                      event.target.checked
                                        ? [...old, key]
                                        : old.filter((value) => value !== key),
                                    )
                                  }
                                />
                              </td>
                              <td>
                                <strong>{key}</strong>
                                {item.cdc_issues.length > 0 && (
                                  <small className="field-error">
                                    {item.cdc_issues.join("; ")}
                                  </small>
                                )}
                              </td>
                              <td>{number(item.estimated_rows)}</td>
                              <td>
                                <code>
                                  {item.primary_key_columns.join(", ") ||
                                    "None"}
                                </code>
                              </td>
                              <td>
                                <Status value={item.cdc_status} />
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
                <p className="muted">{chosen.length} tables selected</p>
                <div className="form-grid">
                  <Field
                    label="Pipeline name"
                    error={form.formState.errors.name?.message}
                  >
                    <Input {...form.register("name")} />
                  </Field>
                  <Field
                    label="Topic prefix"
                    error={form.formState.errors.topic_prefix?.message}
                  >
                    <Input {...form.register("topic_prefix")} />
                  </Field>
                  <Field label="Snapshot mode">
                    <select {...form.register("snapshot_mode")}>
                      <option value="initial">Initial + streaming</option>
                      <option value="no_data">Streaming only</option>
                      <option value="never">Schema only</option>
                    </select>
                  </Field>
                  <details className="advanced full-width">
                    <summary>Advanced capture settings</summary>
                    <div className="form-grid">
                      <Field label="Heartbeat (ms)">
                        <Input
                          type="number"
                          {...form.register("heartbeat_interval_ms", {
                            valueAsNumber: true,
                          })}
                        />
                      </Field>
                      <Field label="Batch size">
                        <Input
                          type="number"
                          {...form.register("max_batch_size", {
                            valueAsNumber: true,
                          })}
                        />
                      </Field>
                      <Field label="Queue size">
                        <Input
                          type="number"
                          {...form.register("max_queue_size", {
                            valueAsNumber: true,
                          })}
                        />
                      </Field>
                      <Field label="Poll interval (ms)">
                        <Input
                          type="number"
                          {...form.register("poll_interval_ms", {
                            valueAsNumber: true,
                          })}
                        />
                      </Field>
                    </div>
                  </details>
                </div>
              </>
            ))}

          {step === 2 && (
            <>
              {(kafka.isError || connect.isError) && (
                <ErrorPanel error={kafka.error || connect.error} />
              )}
              <div className="form-grid">
                <Field label="Kafka cluster">
                  <select
                    value={clusterId}
                    onChange={(event) => {
                      setClusterId(event.target.value);
                      setConnectId("");
                    }}
                  >
                    <option value="">Select Kafka</option>
                    {kafka.data?.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field
                  label="Kafka Connect cluster"
                  hint="Used by Capture and Delivery runtimes"
                >
                  <select
                    value={connectId}
                    onChange={(event) => setConnectId(event.target.value)}
                  >
                    <option value="">Select Connect cluster</option>
                    {connect.data
                      ?.filter((item) => item.kafka_cluster_id === clusterId)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                  </select>
                </Field>
              </div>
              <h3>Topic mapping preview</h3>
              <div className="mapping-list">
                {chosen.map((item) => (
                  <div key={item.id}>
                    <code>
                      {item.schema_name}.{item.table_name}
                    </code>
                    <ArrowRight size={15} />
                    <code>
                      {form.getValues("topic_prefix")}.{item.schema_name}.
                      {item.table_name}
                    </code>
                  </div>
                ))}
              </div>
            </>
          )}

          {step === 3 && (
            <>
              {destinations.isPending ? (
                <Loading />
              ) : destinations.isError ? (
                <ErrorPanel
                  error={destinations.error}
                  retry={() => destinations.refetch()}
                />
              ) : (
                <>
                  <Field label="Destination">
                    <select
                      value={destinationId}
                      onChange={(event) => setDestinationId(event.target.value)}
                    >
                      <option value="">Select an existing destination</option>
                      {destinationOptions.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name} · {item.type} · {item.status}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Button asChild variant="outline">
                    <Link href="/destinations/new">
                      <Plus size={14} /> Create New Destination
                    </Link>
                  </Button>
                  <h3>Delivery Settings</h3>
                  <p className="muted">JDBC Sink · Powered by Kafka Connect</p>
                  <div className="form-grid">
                    <Field label="Delivery name">
                      <Input
                        value={delivery.name}
                        onChange={(event) =>
                          setDelivery((old) => ({
                            ...old,
                            name: event.target.value,
                          }))
                        }
                      />
                    </Field>
                    <Field label="Insert mode">
                      <select
                        value={delivery.write_mode}
                        onChange={(event) =>
                          setDelivery((old) => ({
                            ...old,
                            write_mode: event.target.value as
                              "upsert" | "insert",
                          }))
                        }
                      >
                        <option value="upsert">Upsert</option>
                        <option value="insert">Insert</option>
                      </select>
                    </Field>
                    {(
                      ["delete_enabled", "auto_create", "auto_evolve"] as const
                    ).map((key) => (
                      <label className="check-row" key={key}>
                        <input
                          type="checkbox"
                          checked={delivery[key]}
                          onChange={(event) =>
                            setDelivery((old) => ({
                              ...old,
                              [key]: event.target.checked,
                            }))
                          }
                        />{" "}
                        {key.replaceAll("_", " ")}
                      </label>
                    ))}
                  </div>
                  <h3>Topic → destination table</h3>
                  <div className="mapping-list">
                    {mappings.map((item) => (
                      <div key={item.topic}>
                        <code>{item.topic}</code>
                        <ArrowRight size={15} />
                        <code>
                          {item.schema_name}.{item.table_name}
                        </code>
                      </div>
                    ))}
                  </div>
                  <details className="advanced">
                    <summary>Advanced delivery settings</summary>
                    <JsonView value={{ ...delivery, topics }} />
                  </details>
                </>
              )}
            </>
          )}

          {step === 4 && (
            <>
              <DataFlowRail
                label="Pipeline review"
                stages={[
                  {
                    label: "Source",
                    name: source?.name || "Source",
                    detail: source?.type,
                    status: source?.status,
                  },
                  {
                    label: "Capture",
                    name: form.getValues("name"),
                    detail: `Debezium ${source?.type || ""}`,
                    status: "CREATING",
                  },
                  {
                    label: "Stream",
                    name: stream?.name || "Kafka",
                    detail: `${topics.length} topics`,
                    status: stream?.status,
                  },
                  {
                    label: "Delivery",
                    name: delivery.name,
                    detail: "Kafka Connect JDBC Sink",
                    status: "CREATING",
                  },
                  {
                    label: "Destination",
                    name: destination?.name || "Destination",
                    detail: destination?.type,
                    status: destination?.status,
                  },
                ]}
              />
              <dl className="facts">
                <dt>Source</dt>
                <dd>{source?.name}</dd>
                <dt>Capture</dt>
                <dd>Debezium {source?.type}</dd>
                <dt>Tables</dt>
                <dd>
                  {chosen
                    .map((item) => `${item.schema_name}.${item.table_name}`)
                    .join(", ")}
                </dd>
                <dt>Kafka</dt>
                <dd>{stream?.name}</dd>
                <dt>Topics</dt>
                <dd>{topics.join(", ")}</dd>
                <dt>Delivery</dt>
                <dd>JDBC Sink · {delivery.name}</dd>
                <dt>Destination</dt>
                <dd>{destination?.name}</dd>
                <dt>Connect cluster</dt>
                <dd>{connectCluster?.name}</dd>
              </dl>
              <details className="advanced">
                <summary>Advanced · Capture configuration</summary>
                <JsonView value={preview?.config || {}} />
              </details>
              {partial && (
                <div className="error-panel" role="alert">
                  <div>
                    <strong>{partial.message}</strong>
                    <p>Completed: {partial.completed.join(", ") || "none"}</p>
                  </div>
                  {partial.failedStage === "delivery" && partial.pipeline && (
                    <Button
                      disabled={retry.isPending}
                      onClick={() => retry.mutate()}
                    >
                      {retry.isPending && (
                        <Loader2 className="spin" size={15} />
                      )}{" "}
                      Retry Delivery
                    </Button>
                  )}
                </div>
              )}
              {create.isError && !partial && (
                <ErrorPanel error={create.error} />
              )}
              {retry.isError && <ErrorPanel error={retry.error} />}
            </>
          )}

          {review.isError && <ErrorPanel error={review.error} />}
          <div className="wizard-actions">
            <Button
              variant="outline"
              disabled={step === 0 || create.isPending}
              onClick={() => {
                setPartial(null);
                setStep((value) => value - 1);
              }}
            >
              <ArrowLeft size={15} /> Back
            </Button>
            {step < 4 ? (
              <Button
                disabled={!canContinue || review.isPending}
                onClick={next}
              >
                {review.isPending && <Loader2 className="spin" size={15} />}{" "}
                Continue <ArrowRight size={15} />
              </Button>
            ) : (
              <Button
                disabled={create.isPending || !!partial}
                onClick={() => create.mutate()}
              >
                {create.isPending && <Loader2 className="spin" size={15} />}{" "}
                Create Pipeline
              </Button>
            )}
          </div>
        </section>
        <aside className="wizard-summary">
          <GitBranch size={23} />
          <h3>Pipeline summary</h3>
          <div>
            <small>SOURCE</small>
            <strong>{source?.name || "Choose a source"}</strong>
          </div>
          <div>
            <small>CAPTURE</small>
            <strong>{chosen.length} tables</strong>
          </div>
          <div>
            <small>STREAM</small>
            <strong>{stream?.name || "Choose Kafka"}</strong>
          </div>
          <div>
            <small>DELIVERY</small>
            <strong>{delivery.name}</strong>
          </div>
          <div>
            <small>DESTINATION</small>
            <strong>{destination?.name || "Choose destination"}</strong>
          </div>
        </aside>
      </div>
    </>
  );
}
