"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Database,
  GitBranch,
  Loader2,
  Plus,
  RefreshCw,
} from "lucide-react";
import type {
  ConnectCluster,
  CustomKeyValidation,
  Destination,
  KafkaCluster,
  KeyMode,
  Source,
  SourceTable,
  SyncMode,
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
import { useAuthorization } from "@/lib/auth";
import {
  discoveredTableCount,
  loadSourceMetadata,
} from "@/lib/source-metadata";
import { toast } from "sonner";

const steps = [
  "Source",
  "Infrastructure",
  "Tables",
  "Capture",
  "Delivery",
  "Review",
];
type DeliveryDraft = Omit<DeliveryCreateInput, "pipeline_id" | "mappings">;
type TableChoice = {
  syncMode: SyncMode;
  keyMode: KeyMode;
  keyColumns: string[];
  keyName: string | null;
  currentStateReady: boolean;
};
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

function tableStrategy(table: SourceTable): TableChoice {
  const assessment = table.sync_assessment;
  return {
    syncMode:
      assessment?.recommendation.sync_mode ||
      (table.primary_key_columns.length ? "CURRENT_STATE" : "CHANGE_EVENT_LOG"),
    keyMode:
      assessment?.effective_key.mode ||
      (table.primary_key_columns.length ? "PRIMARY_KEY" : "NONE"),
    keyColumns: assessment?.effective_key.columns || table.primary_key_columns,
    keyName: assessment?.effective_key.constraint_name || null,
    currentStateReady:
      assessment?.capabilities.current_state.status === "SUPPORTED" ||
      (!assessment && table.primary_key_columns.length > 0),
  };
}

export function PipelineWizard() {
  const { can } = useAuthorization();
  const router = useRouter();
  const queryClient = useQueryClient();
  const params = useSearchParams();
  const [step, setStep] = useState(0);
  const [sourceId, setSourceId] = useState(params.get("source") || "");
  const [selected, setSelected] = useState<string[]>([]);
  const [tableChoices, setTableChoices] = useState<Record<string, TableChoice>>(
    {},
  );
  const [configuringTableId, setConfiguringTableId] = useState<string | null>(
    null,
  );
  const [customColumns, setCustomColumns] = useState("");
  const [customValidation, setCustomValidation] =
    useState<CustomKeyValidation | null>(null);
  const [customValidationError, setCustomValidationError] =
    useState<Error | null>(null);
  const [validatingCustomKey, setValidatingCustomKey] = useState(false);
  const [tableSearch, setTableSearch] = useState("");
  const [clusterId, setClusterId] = useState("");
  const [connectId, setConnectId] = useState("");
  const [destinationId, setDestinationId] = useState(
    params.get("destination_id") || "",
  );
  const [captureOnly, setCaptureOnly] = useState(false);
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
  const metadata = useMutation({
    mutationFn: () => loadSourceMetadata(sourceId),
    onSuccess: async (job) => {
      setSelected([]);
      setTableChoices({});
      await queryClient.invalidateQueries({
        queryKey: ["source-tables", sourceId],
      });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["source", sourceId] });
      const count = discoveredTableCount(job);
      toast.success(
        count === null
          ? "Source metadata loaded"
          : `${count} ${count === 1 ? "table" : "tables"} loaded`,
      );
    },
    onError: (error: Error) => toast.error(error.message),
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
  const choiceFor = (table: SourceTable) =>
    tableChoices[`${table.schema_name}.${table.table_name}`] ||
    tableStrategy(table);
  const databaseDeliveryReady = chosen.every((table) => {
    const choice = choiceFor(table);
    return choice.syncMode === "CURRENT_STATE" && choice.currentStateReady;
  });
  const configuringTable = (tables.data || []).find(
    (table) => table.id === configuringTableId,
  );
  const configuringChoice = configuringTable
    ? choiceFor(configuringTable)
    : null;
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
      sync_mode: choiceFor(table).syncMode,
      key_mode: choiceFor(table).keyMode,
      key_columns: choiceFor(table).keyColumns,
      key_name: choiceFor(table).keyName,
    })),
  });

  const configureTable = (table: SourceTable) => {
    setConfiguringTableId(table.id);
    setCustomColumns("");
    setCustomValidation(null);
    setCustomValidationError(null);
  };
  const updateTableChoice = (
    table: SourceTable,
    update: Partial<TableChoice>,
  ) => {
    const key = `${table.schema_name}.${table.table_name}`;
    setTableChoices((current) => ({
      ...current,
      [key]: { ...choiceFor(table), ...update },
    }));
  };
  const validateCustomKey = async () => {
    if (!configuringTable) return;
    const columns = [
      ...new Set(
        customColumns
          .split(",")
          .map((column) => column.trim())
          .filter(Boolean),
      ),
    ];
    if (!columns.length) {
      setCustomValidationError(
        new Error("Enter at least one comma-separated column name"),
      );
      return;
    }
    setValidatingCustomKey(true);
    setCustomValidation(null);
    setCustomValidationError(null);
    try {
      const result = await post<CustomKeyValidation>(
        `/sources/${sourceId}/tables/${configuringTable.id}/effective-key/validate`,
        { columns },
      );
      setCustomValidation(result);
      if (result.status === "DATABASE_ENFORCED") {
        const candidate = configuringTable.sync_assessment?.key_candidates.find(
          (item) =>
            item.eligible &&
            item.columns.length === columns.length &&
            item.columns.every((column, index) => column === columns[index]),
        );
        if (candidate) {
          updateTableChoice(configuringTable, {
            keyMode: candidate.mode,
            keyColumns: candidate.columns,
            keyName: candidate.constraint_name,
          });
        }
      }
    } catch (error) {
      setCustomValidationError(error as Error);
    } finally {
      setValidatingCustomKey(false);
    }
  };
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
      setStep(5);
    },
  });
  const create = useMutation({
    mutationFn: () =>
      createPipelineFlow(
        capturePayload(),
        captureOnly ? null : destinationId,
        captureOnly ? null : deliveryPayload(),
      ),
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
    if (step === 1) return !!clusterId && !!connectId;
    if (step === 2) return chosen.length > 0;
    if (step === 4)
      return (
        captureOnly ||
        (databaseDeliveryReady && !!destinationId && !!delivery.name.trim())
      );
    return true;
  })();
  const next = async () => {
    if (!canContinue) return;
    if (step === 3 && !(await form.trigger())) return;
    if (step === 4) return review.mutate();
    setStep((value) => Math.min(5, value + 1));
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
          <span className="step-label">STEP {step + 1} OF 6</span>
          <h2>
            {
              [
                "Where does your data come from?",
                "Where should captured events run?",
                "Which tables should be captured?",
                "How should capture behave?",
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
                description={
                  can("sources.write")
                    ? "Register a PostgreSQL or MySQL source before creating a pipeline."
                    : "Ask an Admin to register a PostgreSQL or MySQL source."
                }
                href={can("sources.write") ? "/sources/new" : undefined}
                action={can("sources.write") ? "Create Source" : undefined}
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
                          setTableChoices({});
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
                {can("sources.write") && (
                  <Button asChild variant="outline">
                    <Link href="/sources/new">
                      <Plus size={14} /> Create New Source
                    </Link>
                  </Button>
                )}
              </>
            ))}

          {step === 2 &&
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
                    disabled={!tables.data?.length}
                    onChange={(event) => setTableSearch(event.target.value)}
                  />
                  <Button
                    variant="outline"
                    disabled={!eligible.length}
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
                  {can("sources.write") && (
                    <Button
                      variant="outline"
                      disabled={metadata.isPending}
                      onClick={() => metadata.mutate()}
                    >
                      {metadata.isPending ? (
                        <Loader2 className="spin" size={15} />
                      ) : (
                        <RefreshCw size={15} />
                      )}
                      Load metadata
                    </Button>
                  )}
                </div>
                {metadata.isError && <ErrorPanel error={metadata.error} />}
                {!tables.data?.length ? (
                  <Empty
                    title="No table metadata loaded"
                    description={
                      can("sources.write")
                        ? "Load metadata to discover the source schemas, tables, keys, and CDC readiness."
                        : "Ask an Admin or Operator to load metadata for this source."
                    }
                  />
                ) : (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Select</th>
                          <th>Table</th>
                          <th>Rows</th>
                          <th>Effective key</th>
                          <th>Strategy</th>
                          <th>Status</th>
                          <th>Configuration</th>
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
                            const strategy = choiceFor(item);
                            const assessment = item.sync_assessment;
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
                                          : old.filter(
                                              (value) => value !== key,
                                            ),
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
                                    {strategy.keyColumns.join(", ") || "None"}
                                  </code>
                                  <small className="muted">
                                    {strategy.keyMode.replaceAll("_", " ")}
                                  </small>
                                </td>
                                <td>
                                  <strong>
                                    {strategy.syncMode.replaceAll("_", " ")}
                                  </strong>
                                  <small className="muted">
                                    {assessment?.recommendation.reason}
                                  </small>
                                </td>
                                <td>
                                  <Status
                                    value={
                                      strategy.currentStateReady
                                        ? "READY"
                                        : strategy.syncMode ===
                                            "CHANGE_EVENT_LOG"
                                          ? "EVENT LOG READY"
                                          : "INSERT ONLY"
                                    }
                                  />
                                  {assessment?.requirements.map(
                                    (requirement) => (
                                      <small
                                        className="field-error"
                                        key={requirement.code}
                                      >
                                        {requirement.message}
                                      </small>
                                    ),
                                  )}
                                </td>
                                <td>
                                  <Button
                                    type="button"
                                    variant="outline"
                                    onClick={() => configureTable(item)}
                                  >
                                    Configure
                                  </Button>
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="muted">{chosen.length} tables selected</p>
              </>
            ))}

          {step === 1 && (
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
            </>
          )}

          {step === 3 && (
            <>
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
                  hint="Used as the prefix for this pipeline's Kafka topics."
                >
                  <Input {...form.register("topic_prefix")} />
                </Field>
                <Field
                  label="Snapshot mode"
                  hint="Choose how the initial state is captured before streaming changes."
                >
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

          {step === 4 && (
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
                  <div className="choice-grid">
                    <button
                      type="button"
                      className={
                        captureOnly ? "choice-card selected" : "choice-card"
                      }
                      onClick={() => {
                        setCaptureOnly(true);
                        setDestinationId("");
                      }}
                    >
                      <strong>Kafka capture only</strong>
                      <span>
                        Keep each table&apos;s supported event strategy in Kafka
                        without creating a sink connector.
                      </span>
                    </button>
                    <button
                      type="button"
                      disabled={!databaseDeliveryReady}
                      className={
                        !captureOnly ? "choice-card selected" : "choice-card"
                      }
                      onClick={() => setCaptureOnly(false)}
                    >
                      <strong>Database current state</strong>
                      <span>
                        Requires a safe effective key for every selected table.
                      </span>
                    </button>
                  </div>
                  {!databaseDeliveryReady && (
                    <div className="info-strip">
                      One or more tables cannot safely maintain database current
                      state. Use Kafka event log only, or apply the reported
                      source requirements and reload metadata.
                    </div>
                  )}
                  {!captureOnly && (
                    <>
                      <Field label="Destination">
                        <select
                          value={destinationId}
                          onChange={(event) =>
                            setDestinationId(event.target.value)
                          }
                        >
                          <option value="">
                            Select an existing destination
                          </option>
                          {destinationOptions.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.name} · {item.type} · {item.status}
                            </option>
                          ))}
                        </select>
                      </Field>
                      {can("destinations.write") && (
                        <Button asChild variant="outline">
                          <Link href="/destinations/new">
                            <Plus size={14} /> Create New Destination
                          </Link>
                        </Button>
                      )}
                      <h3>Delivery Settings</h3>
                      <p className="muted">
                        JDBC Sink · Powered by Kafka Connect
                      </p>
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
                          [
                            "delete_enabled",
                            "auto_create",
                            "auto_evolve",
                          ] as const
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
            </>
          )}

          {step === 5 && (
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
                    name: captureOnly ? "Kafka capture" : delivery.name,
                    detail: captureOnly
                      ? "No sink connector"
                      : "Kafka Connect JDBC Sink",
                    status: captureOnly ? "READY" : "CREATING",
                  },
                  {
                    label: "Destination",
                    name: captureOnly
                      ? "Capture only"
                      : destination?.name || "Destination",
                    detail: captureOnly ? "Kafka topics" : destination?.type,
                    status: captureOnly ? "READY" : destination?.status,
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
                    .map((item) => {
                      const strategy = choiceFor(item);
                      return `${item.schema_name}.${item.table_name} · ${strategy.syncMode} · ${strategy.keyMode}: ${strategy.keyColumns.join(", ") || "none"}`;
                    })
                    .join("; ")}
                </dd>
                <dt>Initial load</dt>
                <dd>{form.getValues("snapshot_mode").replaceAll("_", " ")}</dd>
                <dt>Limitations</dt>
                <dd>
                  {chosen
                    .flatMap((item) => [
                      ...(item.sync_assessment?.requirements || []).map(
                        (requirement) =>
                          `${item.schema_name}.${item.table_name}: ${requirement.message}`,
                      ),
                      ...(item.sync_assessment?.warnings || []).map(
                        (warning) =>
                          `${item.schema_name}.${item.table_name}: ${warning.message}`,
                      ),
                    ])
                    .join("; ") || "None"}
                </dd>
                <dt>Kafka</dt>
                <dd>{stream?.name}</dd>
                <dt>Topics</dt>
                <dd>{topics.join(", ")}</dd>
                <dt>Delivery</dt>
                <dd>
                  {captureOnly
                    ? "Kafka capture only"
                    : `JDBC Sink · ${delivery.name}`}
                </dd>
                <dt>Destination</dt>
                <dd>{captureOnly ? "None" : destination?.name}</dd>
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
            {step < 5 ? (
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
            <strong>{captureOnly ? "Kafka capture" : delivery.name}</strong>
          </div>
          <div>
            <small>DESTINATION</small>
            <strong>
              {captureOnly
                ? "Capture only"
                : destination?.name || "Choose destination"}
            </strong>
          </div>
        </aside>
      </div>
      {configuringTable && configuringChoice && (
        <>
          <div
            className="dialog-overlay"
            aria-hidden="true"
            onClick={() => setConfiguringTableId(null)}
          />
          <section
            className="dialog-content"
            role="dialog"
            aria-modal="true"
            aria-labelledby="table-strategy-title"
          >
            <div className="dialog-heading">
              <span id="table-strategy-title">Configure synchronization</span>
              <button
                type="button"
                aria-label="Close"
                onClick={() => setConfiguringTableId(null)}
              >
                ×
              </button>
            </div>
            <p className="muted">
              {configuringTable.schema_name}.{configuringTable.table_name}
            </p>
            <div className="form-grid">
              <Field label="Sync strategy">
                <select
                  value={configuringChoice.syncMode}
                  onChange={(event) =>
                    updateTableChoice(configuringTable, {
                      syncMode: event.target.value as SyncMode,
                    })
                  }
                >
                  {(
                    [
                      ["CURRENT_STATE", "Current state", "current_state"],
                      [
                        "CHANGE_EVENT_LOG",
                        "Change event log",
                        "change_event_log",
                      ],
                      ["APPEND_ONLY", "Append only", "append_only"],
                    ] as const
                  ).map(([value, label, capability]) => {
                    const status =
                      configuringTable.sync_assessment?.capabilities[capability]
                        .status ||
                      (value === "CURRENT_STATE" &&
                      configuringTable.primary_key_columns.length
                        ? "SUPPORTED"
                        : "UNKNOWN");
                    return (
                      <option
                        key={value}
                        value={value}
                        disabled={status !== "SUPPORTED"}
                      >
                        {label} · {status.replaceAll("_", " ")}
                      </option>
                    );
                  })}
                </select>
              </Field>
              <Field
                label="Effective row key"
                hint="Required for current-state database delivery"
              >
                <select
                  value={
                    configuringTable.sync_assessment?.key_candidates.findIndex(
                      (candidate) =>
                        candidate.mode === configuringChoice.keyMode &&
                        candidate.columns.length ===
                          configuringChoice.keyColumns.length &&
                        candidate.columns.every(
                          (column, index) =>
                            column === configuringChoice.keyColumns[index],
                        ),
                    ) ?? -1
                  }
                  onChange={(event) => {
                    const candidate =
                      configuringTable.sync_assessment?.key_candidates[
                        Number(event.target.value)
                      ];
                    if (candidate)
                      updateTableChoice(configuringTable, {
                        keyMode: candidate.mode,
                        keyColumns: candidate.columns,
                        keyName: candidate.constraint_name,
                      });
                  }}
                >
                  {!configuringTable.sync_assessment?.key_candidates.some(
                    (candidate) => candidate.eligible,
                  ) && <option value={-1}>No eligible row key</option>}
                  {configuringTable.sync_assessment?.key_candidates.map(
                    (candidate, index) => (
                      <option
                        key={`${candidate.mode}-${candidate.constraint_name}-${candidate.columns.join("-")}`}
                        value={index}
                        disabled={!candidate.eligible}
                      >
                        {candidate.mode.replaceAll("_", " ")} ·{" "}
                        {candidate.columns.join(", ")} ·{" "}
                        {candidate.eligible ? "eligible" : candidate.reason}
                      </option>
                    ),
                  )}
                </select>
              </Field>
            </div>
            <div className="info-strip">
              {configuringTable.sync_assessment?.capabilities[
                configuringChoice.syncMode === "CURRENT_STATE"
                  ? "current_state"
                  : configuringChoice.syncMode === "CHANGE_EVENT_LOG"
                    ? "change_event_log"
                    : "append_only"
              ].reason || "Reload metadata to calculate table capabilities."}
            </div>
            {(configuringTable.sync_assessment?.requirements.length || 0) >
              0 && (
              <div className="error-panel" role="alert">
                <div>
                  <strong>Source configuration required</strong>
                  {configuringTable.sync_assessment?.requirements.map(
                    (requirement) => (
                      <div key={requirement.code}>
                        <p>{requirement.message}</p>
                        {requirement.suggested_sql && (
                          <code>{requirement.suggested_sql}</code>
                        )}
                      </div>
                    ),
                  )}
                </div>
              </div>
            )}
            <details className="advanced">
              <summary>Advanced · Validate custom key</summary>
              <p className="muted">
                Validation checks column existence, NULLs, duplicates, and
                database enforcement with bounded queries. A verified-only key
                is diagnostic and cannot enable safe current-state sync.
              </p>
              <Field label="Columns" hint="Comma-separated, in key order">
                <Input
                  value={customColumns}
                  placeholder="tenant_id, external_id"
                  onChange={(event) => setCustomColumns(event.target.value)}
                />
              </Field>
              {can("sources.write") ? (
                <Button
                  type="button"
                  variant="outline"
                  disabled={validatingCustomKey}
                  onClick={validateCustomKey}
                >
                  {validatingCustomKey && (
                    <Loader2 className="spin" size={15} />
                  )}
                  Validate key
                </Button>
              ) : (
                <p className="muted">
                  Ask an Admin or Operator to validate a custom key.
                </p>
              )}
              {customValidationError && (
                <ErrorPanel error={customValidationError} />
              )}
              {customValidation && (
                <div className="info-strip">
                  <strong>
                    {customValidation.status.replaceAll("_", " ")}
                  </strong>
                  {customValidation.errors.map((error) => (
                    <p key={error}>{error}</p>
                  ))}
                  {customValidation.warning && (
                    <p>{customValidation.warning}</p>
                  )}
                  {customValidation.suggested_sql && (
                    <code>{customValidation.suggested_sql}</code>
                  )}
                </div>
              )}
            </details>
            <div className="dialog-actions">
              <Button type="button" onClick={() => setConfiguringTableId(null)}>
                Done
              </Button>
            </div>
          </section>
        </>
      )}
    </>
  );
}
