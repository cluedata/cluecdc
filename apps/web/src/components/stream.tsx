"use client";
import { InventoryStrip } from "./operational";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  ArrowRight,
  Eye,
  Loader2,
  Radio,
  RefreshCw,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import type {
  CDCEvent,
  EventSample,
  KafkaCluster,
  Pipeline,
  Topic,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { ApiError, api, date, number } from "@/lib/api";
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

import { DetailDrawer, Tooltip } from "./operational";

export function EventsExplorer({
  initialCluster = "",
  initialTopic = "",
  allowedTopics,
}: {
  initialCluster?: string;
  initialTopic?: string;
  allowedTopics?: string[];
}) {
  const [cluster, setCluster] = useState(initialCluster);
  const [topic, setTopic] = useState(initialTopic);
  const [pipeline, setPipeline] = useState("");
  const [operation, setOperation] = useState("");
  const [partition, setPartition] = useState("");
  const [offset, setOffset] = useState("");
  const [table, setTable] = useState("");
  const [key, setKey] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [selected, setSelected] = useState<CDCEvent | null>(null);
  const [view, setView] = useState("Formatted");
  const clusters = useQuery({
    queryKey: ["kafka-clusters"],
    queryFn: () => api<KafkaCluster[]>("/kafka/clusters"),
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
  });
  const topics = useQuery({
    queryKey: ["topics", cluster],
    queryFn: () => api<Topic[]>(`/kafka/topics?cluster_id=${cluster}`),
    enabled: !!cluster && !allowedTopics,
  });
  const sample = useMutation({
    mutationFn: () => {
      const q = new URLSearchParams({
        cluster_id: cluster,
        topic,
        limit: "100",
        ...(operation ? { operation } : {}),
        ...(partition ? { partition } : {}),
        ...(offset ? { offset } : {}),
        ...(table ? { table } : {}),
        ...(key ? { key } : {}),
        ...(start ? { start_ms: String(new Date(start).getTime()) } : {}),
        ...(end ? { end_ms: String(new Date(end).getTime()) } : {}),
      });
      return api<EventSample>(`/events?${q}`);
    },
  });
  const names = allowedTopics || topics.data?.map((t) => t.name) || [];
  const filteredNames = pipeline
    ? names.filter((n) =>
        n.startsWith(
          `${pipelines.data?.find((p) => p.id === pipeline)?.topic_prefix}.`,
        ),
      )
    : names;
  return (
    <>
      <section className="panel">
        <div className="section-heading">
          <h2>Event explorer</h2>
          <span className="muted">Bounded, read-only Kafka samples</span>
        </div>
        <div className="event-filters">
          <Field label="Kafka cluster">
            <select
              value={cluster}
              onChange={(e) => {
                setCluster(e.target.value);
                setTopic("");
                sample.reset();
              }}
              disabled={!!initialCluster}
            >
              <option value="">Select cluster</option>
              {clusters.data?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Pipeline">
            <select
              value={pipeline}
              onChange={(e) => setPipeline(e.target.value)}
            >
              <option value="">All pipelines</option>
              {pipelines.data
                ?.filter((p) => p.kafka_cluster_id === cluster)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Topic">
            <select
              value={topic}
              onChange={(e) => {
                setTopic(e.target.value);
                sample.reset();
              }}
            >
              <option value="">Select topic</option>
              {filteredNames.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </Field>
          <Field label="Operation">
            <select
              value={operation}
              onChange={(e) => setOperation(e.target.value)}
            >
              <option value="">All operations</option>
              {["CREATE", "UPDATE", "DELETE", "READ"].map((op) => (
                <option key={op}>{op}</option>
              ))}
            </select>
          </Field>
        </div>
        <details className="advanced">
          <summary>Advanced filters</summary>
          <div className="event-filters">
            <Field label="Table">
              <Input
                value={table}
                onChange={(e) => setTable(e.target.value)}
                placeholder="customers"
              />
            </Field>
            <Field label="Partition">
              <Input
                type="number"
                min={0}
                value={partition}
                onChange={(e) => setPartition(e.target.value)}
              />
            </Field>
            <Field label="Minimum offset">
              <Input
                type="number"
                min={0}
                value={offset}
                onChange={(e) => setOffset(e.target.value)}
              />
            </Field>
            <Field label="Primary key contains">
              <Input value={key} onChange={(e) => setKey(e.target.value)} />
            </Field>
            <Field label="From">
              <Input
                type="datetime-local"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </Field>
            <Field label="Until">
              <Input
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </Field>
          </div>
        </details>
        <div className="toolbar">
          <Button
            disabled={!cluster || !topic || sample.isPending}
            onClick={() => sample.mutate()}
          >
            {sample.isPending ? (
              <Loader2 size={15} className="spin" />
            ) : (
              <RefreshCw size={15} />
            )}
            Fetch recent events
          </Button>
          <span className="muted">
            At most 1,000 records scanned · 2 MB · no offset commits
          </span>
        </div>
        {clusters.isError && <ErrorPanel error={clusters.error} />}{" "}
        {topics.isError && <ErrorPanel error={topics.error} />}{" "}
        {sample.isError && <ErrorPanel error={sample.error} />}
      </section>
      {sample.data ? (
        <>
          <div className="info-strip">
            <Eye size={17} />
            {sample.data.notice} · {sample.data.scanned} scanned
          </div>
          <DataTable
            onRowClick={(e) => setSelected(e)}
            data={sample.data.events}
            columns={[
              {
                accessorKey: "timestamp",
                header: "Timestamp",
                cell: ({ row }) => date(row.original.timestamp),
              },
              {
                accessorKey: "operation",
                header: "Operation",
                cell: ({ row }) => <Status value={row.original.operation} />,
              },
              { accessorKey: "partition", header: "Partition" },
              { accessorKey: "offset", header: "Offset" },
              {
                accessorKey: "key",
                header: "Key",
                cell: ({ row }) => (
                  <code>{JSON.stringify(row.original.key)}</code>
                ),
              },
              {
                id: "open",
                header: "Details",
                cell: ({ row }) => (
                  <Button
                    variant="ghost"
                    onClick={() => setSelected(row.original)}
                  >
                    Inspect
                    <ArrowRight size={14} />
                  </Button>
                ),
              },
            ]}
          />
        </>
      ) : (
        !sample.isPending && (
          <Empty
            title="Inspect the change stream"
            description="Choose a Kafka topic and fetch a bounded sample of recent events."
          />
        )
      )}
      <DetailDrawer
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title={
          selected
            ? `${selected.operation} · partition ${selected.partition} · offset ${selected.offset}`
            : "Event"
        }
        description={selected ? date(selected.timestamp) : "CDC event details"}
      >
        {selected && (
          <>
            <Tabs
              tabs={["Formatted", "Raw JSON"]}
              active={view}
              onChange={setView}
            />
            {view === "Raw JSON" ? (
              <JsonView value={selected.raw} />
            ) : (
              <>
                <h3>Primary key</h3>
                <JsonView value={selected.key} />
                <div className="before-after">
                  <div>
                    <h3>Before</h3>
                    <JsonView value={selected.before} />
                  </div>
                  <div>
                    <h3>After</h3>
                    <JsonView value={selected.after} />
                  </div>
                </div>
                <h3>Changed fields</h3>
                <JsonView value={selected.changed_fields} />
                <h3>Source metadata</h3>
                <JsonView value={selected.source} />
              </>
            )}
          </>
        )}
      </DetailDrawer>
    </>
  );
}
export function EventsPage() {
  return (
    <>
      <PageHeader
        title="CDC events"
        description="Inspect source changes, before and after values, and Kafka record metadata."
        eyebrow="DATA / EVENTS"
      />
      <EventsExplorer />
    </>
  );
}

export function TopicsPage({
  name,
  initialCluster = "",
}: {
  name?: string;
  initialCluster?: string;
}) {
  const [cluster, setCluster] = useState(initialCluster);
  const [tab, setTab] = useState("Overview");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const deleteInFlight = useRef(false);
  const router = useRouter();
  const queryClient = useQueryClient();
  const clusters = useQuery({
    queryKey: ["kafka-clusters"],
    queryFn: () => api<KafkaCluster[]>("/kafka/clusters"),
  });
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => api<{ permissions: string[] }>("/session"),
    staleTime: 30000,
  });
  const topics = useQuery({
    queryKey: ["topics", cluster, name],
    queryFn: () =>
      api<Topic[] | Topic>(
        `/kafka/topics${name ? `/${encodeURIComponent(name)}` : ""}?cluster_id=${cluster}`,
      ),
    enabled: !!cluster,
  });
  const data = topics.data && !Array.isArray(topics.data) ? topics.data : null;
  const permissions = session.data?.permissions || [];
  const canDelete =
    permissions.includes("*") || permissions.includes("kafka.topic.delete");
  const remove = useMutation({
    mutationFn: () =>
      api<{ deleted: boolean; topic: string }>(
        `/kafka/topics/${encodeURIComponent(name || "")}?cluster_id=${encodeURIComponent(cluster)}`,
        { method: "DELETE" },
      ),
    onSuccess: async () => {
      queryClient.removeQueries({
        queryKey: ["topics", cluster, name],
        exact: true,
      });
      await queryClient.invalidateQueries({ queryKey: ["topics", cluster] });
      setDeleteOpen(false);
      toast.success(`Topic ${name} was deleted`);
      router.replace("/kafka/topics");
    },
    onSettled: () => {
      deleteInFlight.current = false;
    },
  });
  return (
    <>
      {name && (
        <Link className="back-link" href="/kafka/topics">
          <ArrowLeft size={14} />
          All topics
        </Link>
      )}
      <PageHeader
        title={name || "Kafka topics"}
        description="Explore live Kafka metadata and bounded message samples."
        eyebrow="STREAM / KAFKA"
      >
        {name && data && canDelete && (
          <Button
            variant="destructive"
            disabled={data.protected}
            title={data.protection_reason || undefined}
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 size={15} />
            Delete topic
          </Button>
        )}
      </PageHeader>
      {cluster && Array.isArray(topics.data) && (
        <InventoryStrip
          items={[
            { label: "Topics", value: topics.data.length },
            {
              label: "Partitions",
              value: topics.data.reduce((n, t) => n + t.partitions, 0),
            },
            {
              label: "Kafka cluster",
              value:
                clusters.data?.find((c) => c.id === cluster)?.name ||
                "Unavailable",
            },
          ]}
        />
      )}
      <div className="filters">
        <Radio size={18} />
        <select
          aria-label="Kafka cluster"
          value={cluster}
          onChange={(e) => setCluster(e.target.value)}
        >
          <option value="">Choose Kafka cluster</option>
          {clusters.data?.map((c) => (
            <option value={c.id} key={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <Button
          variant="outline"
          disabled={!cluster}
          onClick={() => topics.refetch()}
        >
          <RefreshCw size={15} />
          Refresh
        </Button>
      </div>
      {clusters.isError && <ErrorPanel error={clusters.error} />}{" "}
      {!cluster ? (
        <Empty
          title="Choose a Kafka cluster"
          description="Topic metadata is queried directly from your registered Kafka cluster."
        />
      ) : topics.isPending ? (
        <Loading />
      ) : topics.isError ? (
        topics.error instanceof ApiError &&
        topics.error.code === "TOPIC_NOT_FOUND" ? (
          <Empty
            title="Topic not found"
            description="This topic no longer exists in the selected Kafka cluster."
            href="/kafka/topics"
            action="Back to topics"
          />
        ) : (
          <ErrorPanel error={topics.error} retry={() => topics.refetch()} />
        )
      ) : Array.isArray(topics.data) ? (
        <DataTable
          getRowHref={(t) =>
            `/kafka/topics/${encodeURIComponent(t.name)}?cluster=${cluster}`
          }
          data={topics.data}
          columns={[
            {
              accessorKey: "name",
              header: "Topic",
              cell: ({ row }) => (
                <Link
                  className="text-link"
                  href={`/kafka/topics/${encodeURIComponent(row.original.name)}?cluster=${cluster}`}
                >
                  <code>{row.original.name}</code>
                </Link>
              ),
            },
            { accessorKey: "partitions", header: "Partitions" },
            { accessorKey: "replication_factor", header: "Replication factor" },
            {
              accessorKey: "message_rate",
              header: "Message rate",
              cell: () => "Unavailable",
            },
            {
              accessorKey: "retention",
              header: () => (
                <span>
                  Retention{" "}
                  <Tooltip text="Retention controls how long Kafka keeps records. It does not indicate downstream delivery or event freshness." />
                </span>
              ),
              cell: () => "Unavailable",
            },
            { accessorKey: "size", header: "Size", cell: () => "Unavailable" },
          ]}
        />
      ) : (
        data && (
          <>
            <Tabs
              tabs={[
                "Overview",
                "Messages",
                "Partitions",
                "Consumers",
                "Configuration",
              ]}
              active={tab}
              onChange={setTab}
            />
            {tab === "Overview" && (
              <section className="panel">
                <h2>Topic metadata</h2>
                <dl className="facts">
                  <dt>Partitions</dt>
                  <dd>{data.partitions}</dd>
                  <dt>Replication factor</dt>
                  <dd>{number(data.replication_factor)}</dd>
                  <dt>Rate / size / retention</dt>
                  <dd>Unavailable</dd>
                </dl>
              </section>
            )}
            {tab === "Messages" && (
              <EventsExplorer
                initialCluster={cluster}
                initialTopic={data.name}
                allowedTopics={[data.name]}
              />
            )}{" "}
            {tab === "Partitions" && (
              <section className="panel">
                <h2>Partition metadata</h2>
                <JsonView value={data.partition_details} />
              </section>
            )}{" "}
            {(tab === "Consumers" || tab === "Configuration") && (
              <section className="panel">
                <h2>{tab}</h2>
                <p className="muted">
                  Unavailable. This release retrieves broker topic and partition
                  metadata; consumer group offsets and topic configuration
                  inspection are planned for the operations phase.
                </p>
              </section>
            )}
          </>
        )
      )}
      {name && data && (
        <DeleteTopicDialog
          open={deleteOpen}
          onOpenChange={(open) => {
            if (!remove.isPending) {
              setDeleteOpen(open);
              if (!open) remove.reset();
            }
          }}
          topic={data}
          pending={remove.isPending}
          error={remove.error}
          onDelete={() => {
            if (!deleteInFlight.current) {
              deleteInFlight.current = true;
              remove.mutate();
            }
          }}
        />
      )}
    </>
  );
}

export function DeleteTopicDialog({
  open,
  onOpenChange,
  topic,
  pending,
  error,
  onDelete,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  topic: Topic;
  pending: boolean;
  error: unknown;
  onDelete: () => void;
}) {
  const [confirmation, setConfirmation] = useState("");
  const close = () => {
    if (!pending) {
      setConfirmation("");
      onOpenChange(false);
    }
  };
  const activePipelines = (topic.used_by_pipelines || []).filter(
    (pipeline) => pipeline.active,
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) close();
      }}
      title="Delete Kafka topic?"
      description="This action permanently deletes the topic and its data. This operation cannot be undone."
    >
      <p>
        Topic: <code>{topic.name}</code>
      </p>
      {activePipelines.length > 0 && (
        <div className="warning-strip" role="alert">
          <TriangleAlert size={18} />
          <span>
            This topic is used by active pipeline
            {activePipelines.length === 1 ? "" : "s"}:{" "}
            <strong>
              {activePipelines.map((pipeline) => pipeline.name).join(", ")}
            </strong>
          </span>
        </div>
      )}
      <Field
        label={`Type “${topic.name}” to confirm`}
        hint="The value must match the full topic name exactly."
      >
        <Input
          autoComplete="off"
          value={confirmation}
          disabled={pending}
          onChange={(event) => setConfirmation(event.target.value)}
        />
      </Field>
      {error !== null && error !== undefined && <ErrorPanel error={error} />}
      <div className="dialog-actions">
        <Button variant="outline" disabled={pending} onClick={close}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          disabled={pending || confirmation !== topic.name}
          onClick={onDelete}
        >
          {pending && <Loader2 className="spin" size={15} />}
          {pending ? "Deleting…" : "Delete topic"}
        </Button>
      </div>
    </Dialog>
  );
}
