"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { Connection, Pipeline, PipelineDetail } from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { api, post } from "@/lib/api";
import {
  ErrorPanel,
  Field,
  JsonView,
  Loading,
  PageHeader,
} from "@/components/common";

const defaultTemplate =
  "{{topic}}/year={{timestamp:unit=yyyy}}/month={{timestamp:unit=MM}}/day={{timestamp:unit=dd}}/{{topic}}-{{partition:padding=true}}-{{start_offset:padding=true}}.jsonl.gz";

export function ObjectStorageDeliveryCreatePage() {
  const router = useRouter();
  const search = useSearchParams();
  const [destinationId, setDestinationId] = useState(
    search.get("destination_id") || "",
  );
  const [pipelineId, setPipelineId] = useState("");
  const [name, setName] = useState("Object storage archive");
  const [topics, setTopics] = useState<string[]>([]);
  const [compression, setCompression] = useState<"gzip" | "none">("gzip");
  const [fileMaxRecords, setFileMaxRecords] = useState(100000);
  const [flushIntervalMs, setFlushIntervalMs] = useState(60000);
  const [tasksMax, setTasksMax] = useState(1);
  const [errorPolicy, setErrorPolicy] = useState<"fail" | "continue">("fail");
  const [template, setTemplate] = useState(defaultTemplate);
  const [preview, setPreview] = useState<unknown>(null);
  const destinations = useQuery({
    queryKey: ["connections", "DESTINATION"],
    queryFn: () =>
      api<Connection[]>(
        "/connections?category=OBJECT_STORAGE&capability=DESTINATION",
      ),
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
  const payload = () => ({
    pipeline_id: pipelineId,
    name,
    delivery_type: "OBJECT_STORAGE",
    topics,
    output_format: "JSONL",
    compression,
    file_max_records: fileMaxRecords,
    flush_interval_ms: flushIntervalMs,
    tasks_max: tasksMax,
    error_policy: errorPolicy,
    file_name_template:
      compression === "none" ? template.replace(/\.gz$/, "") : template,
  });
  const validate = useMutation({
    mutationFn: () => post(`/destinations/${destinationId}/preview`, payload()),
    onSuccess: setPreview,
  });
  const deploy = useMutation({
    mutationFn: () =>
      post<{ id: string }>(`/destinations/${destinationId}/deploy`, payload()),
    onSuccess: (delivery) => router.push(`/deliveries/${delivery.id}`),
  });
  const ready =
    !!destinationId && !!pipelineId && !!name.trim() && topics.length > 0;
  return (
    <>
      <PageHeader
        title="New object storage delivery"
        description="Archive CDC events from Kafka to AWS S3 or MinIO as JSONL files."
        eyebrow="DELIVERIES / NEW"
      />
      <section className="panel source-form-page">
        <div className="panel-body">
          <div className="form-grid">
            <Field label="Name">
              <Input
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  setPreview(null);
                }}
              />
            </Field>
            <Field label="Object storage connection">
              <select
                value={destinationId}
                onChange={(event) => {
                  setDestinationId(event.target.value);
                  setPreview(null);
                }}
              >
                <option value="">Choose a destination</option>
                {destinations.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.provider}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Capture pipeline">
              <select
                value={pipelineId}
                onChange={(event) => {
                  setPipelineId(event.target.value);
                  setTopics([]);
                  setPreview(null);
                }}
              >
                <option value="">Choose a deployed pipeline</option>
                {pipelines.data
                  ?.filter((item) => item.connector_id)
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
          {destinations.isPending ||
          pipelines.isPending ||
          (pipeline.isPending && !!pipelineId) ? (
            <Loading />
          ) : null}
          {destinations.isError && <ErrorPanel error={destinations.error} />}
          {pipelines.isError && <ErrorPanel error={pipelines.error} />}
          {pipeline.isError && <ErrorPanel error={pipeline.error} />}
          {pipeline.data && (
            <Field label="Kafka topics">
              <div className="mapping-editor">
                {pipeline.data.tables.map((table) => (
                  <label className="check-row" key={table.topic_name}>
                    <input
                      type="checkbox"
                      checked={topics.includes(table.topic_name)}
                      onChange={(event) => {
                        setTopics((old) =>
                          event.target.checked
                            ? [...old, table.topic_name]
                            : old.filter((topic) => topic !== table.topic_name),
                        );
                        setPreview(null);
                      }}
                    />{" "}
                    {table.topic_name}
                  </label>
                ))}
              </div>
            </Field>
          )}
          <div className="form-grid">
            <Field label="Compression">
              <select
                value={compression}
                onChange={(event) => {
                  setCompression(event.target.value as "gzip" | "none");
                  setPreview(null);
                }}
              >
                <option value="gzip">gzip</option>
                <option value="none">None</option>
              </select>
            </Field>
            <Field label="Maximum records per file">
              <Input
                type="number"
                min={1}
                value={fileMaxRecords}
                onChange={(event) => {
                  setFileMaxRecords(Number(event.target.value));
                  setPreview(null);
                }}
              />
            </Field>
            <Field label="Flush interval (milliseconds)">
              <Input
                type="number"
                min={1000}
                value={flushIntervalMs}
                onChange={(event) => {
                  setFlushIntervalMs(Number(event.target.value));
                  setPreview(null);
                }}
              />
            </Field>
            <Field label="Maximum tasks">
              <Input
                type="number"
                min={1}
                max={4}
                value={tasksMax}
                onChange={(event) => {
                  setTasksMax(Number(event.target.value));
                  setPreview(null);
                }}
              />
            </Field>
            <Field label="Error policy">
              <select
                value={errorPolicy}
                onChange={(event) => {
                  setErrorPolicy(event.target.value as "fail" | "continue");
                  setPreview(null);
                }}
              >
                <option value="fail">Stop on error</option>
                <option value="continue">Continue past bad records</option>
              </select>
            </Field>
            <Field label="Object key template">
              <Input
                value={template}
                onChange={(event) => {
                  setTemplate(event.target.value);
                  setPreview(null);
                }}
              />
            </Field>
          </div>
          <p className="muted">
            JSONL preserves the Debezium event envelope, including delete
            operations. Files are grouped by topic, partition and starting
            offset; replays may produce duplicate event content.
          </p>
          {validate.isError && <ErrorPanel error={validate.error} />}
          {deploy.isError && <ErrorPanel error={deploy.error} />}
          {preview != null && (
            <details className="advanced">
              <summary>Validated connector configuration (redacted)</summary>
              <JsonView value={preview} />
            </details>
          )}
          <div className="wizard-actions">
            <Button asChild variant="outline">
              <Link href="/deliveries/new">Database delivery</Link>
            </Button>
            <Button
              variant="outline"
              disabled={!ready || validate.isPending}
              onClick={() => validate.mutate()}
            >
              Validate
            </Button>
            <Button
              disabled={!ready || !preview || deploy.isPending}
              onClick={() => deploy.mutate()}
            >
              Deploy delivery
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
