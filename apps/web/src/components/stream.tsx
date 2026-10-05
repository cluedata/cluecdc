"use client";
import { InventoryStrip } from "./operational";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  Loader2,
  Radio,
  RefreshCw,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import type { KafkaCluster, Topic } from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { ApiError, api, number } from "@/lib/api";
import {
  DataTable,
  Empty,
  ErrorPanel,
  Field,
  JsonView,
  Loading,
  PageHeader,
  Tabs,
} from "./common";

import { Tooltip } from "./operational";

export function EventsExplorer({
  initialCluster = "",
  initialTopic = "",
  allowedTopics = [],
}: {
  initialCluster?: string;
  initialTopic?: string;
  allowedTopics?: string[];
}) {
  return (
    <section className="panel">
      <h2>CDC payloads stay in the data plane</h2>
      <p>
        ClueCDC does not read or return CDC records. Inspect Kafka directly or
        read destination objects to verify before/after values.
      </p>
      {initialCluster && (
        <Link
          className="text-link"
          href={
            initialTopic
              ? `/kafka/topics/${encodeURIComponent(initialTopic)}?cluster=${initialCluster}`
              : `/kafka/topics?cluster=${initialCluster}`
          }
        >
          View topic metadata
        </Link>
      )}
      {allowedTopics.length > 0 && (
        <p className="muted">{allowedTopics.length} captured topics</p>
      )}
    </section>
  );
}
export function EventsPage() {
  return (
    <>
      <PageHeader
        title="CDC data plane"
        description="Inspect records directly in Kafka or your destination, not through the control-plane API."
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
        description="Explore live Kafka metadata without reading CDC payloads."
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
