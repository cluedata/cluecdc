"use client";
import { InventoryStrip } from "./operational";
import Link from "next/link";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Box, Pencil, Plus, Radio, Trash2 } from "lucide-react";
import { toast } from "sonner";
import type {
  ConnectCluster,
  Connector,
  KafkaCluster,
  Runtime,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { api } from "@/lib/api";
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
import { DetailDrawer, FilterBar } from "./operational";
type Cluster = KafkaCluster | ConnectCluster;
export function ClustersPage({ kind }: { kind: "kafka" | "connect" }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Cluster | null>(null);
  const [deleting, setDeleting] = useState<Cluster | null>(null);
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [protocol, setProtocol] = useState("PLAINTEXT");
  const [kafkaId, setKafkaId] = useState("");
  const key = `${kind}-clusters`;
  const query = useQuery({
    queryKey: [key],
    queryFn: () => api<Cluster[]>(`/${kind}/clusters`),
  });
  const kafka = useQuery({
    queryKey: ["kafka-clusters"],
    queryFn: () => api<KafkaCluster[]>("/kafka/clusters"),
    enabled: kind === "connect",
  });
  const save = useMutation({
    mutationFn: () =>
      api<Cluster>(`/${kind}/clusters${editing ? `/${editing.id}` : ""}`, {
        method: editing ? "PUT" : "POST",
        body: JSON.stringify(
          kind === "kafka"
            ? { name, bootstrap_servers: address, security_protocol: protocol }
            : { name, base_url: address, kafka_cluster_id: kafkaId },
        ),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: [key] });
      setOpen(false);
      toast.success("Cluster configuration saved");
    },
    onError: (e) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: () =>
      api(`/${kind}/clusters/${deleting?.id}`, { method: "DELETE" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: [key] });
      setDeleting(null);
      toast.success("Cluster deleted");
    },
    onError: (e) => toast.error(e.message),
  });
  const edit = (cluster: Cluster | null) => {
    setEditing(cluster);
    setName(
      cluster?.name || (kind === "kafka" ? "Local Kafka" : "Local Connect"),
    );
    setAddress(
      cluster
        ? "bootstrap_servers" in cluster
          ? cluster.bootstrap_servers
          : cluster.base_url
        : kind === "kafka"
          ? "kafka:29092"
          : "http://kafka-connect:8083",
    );
    setKafkaId(
      cluster && "kafka_cluster_id" in cluster
        ? cluster.kafka_cluster_id
        : kafka.data?.[0]?.id || "",
    );
    setProtocol(
      cluster && "security_protocol" in cluster
        ? cluster.security_protocol
        : "PLAINTEXT",
    );
    save.reset();
    setOpen(true);
  };
  const columns: ColumnDef<Cluster>[] = [
    {
      accessorKey: "name",
      header: "Cluster",
      cell: ({ row }) => (
        <span className="entity-link">
          <span className="table-icon">
            {kind === "kafka" ? <Radio size={16} /> : <Box size={16} />}
          </span>
          {row.original.name}
        </span>
      ),
    },
    {
      id: "address",
      header: kind === "kafka" ? "Bootstrap servers" : "REST endpoint",
      cell: ({ row }) => (
        <code>
          {"bootstrap_servers" in row.original
            ? row.original.bootstrap_servers
            : row.original.base_url}
        </code>
      ),
    },
    {
      id: "security",
      header: kind === "kafka" ? "Security" : "Kafka cluster",
      cell: ({ row }) => {
        const cluster = row.original;
        return "security_protocol" in cluster
          ? cluster.security_protocol
          : kafka.data?.find((k) => k.id === cluster.kafka_cluster_id)?.name ||
              cluster.kafka_cluster_id;
      },
    },
    {
      accessorKey: "status",
      header: "Last observed status",
      cell: ({ row }) => <Status value={row.original.status} />,
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <div className="toolbar">
          <Button
            variant="ghost"
            aria-label={`Edit ${row.original.name}`}
            onClick={() => edit(row.original)}
          >
            <Pencil size={15} />
          </Button>
          <Button
            variant="ghost"
            aria-label={`Delete ${row.original.name}`}
            onClick={() => setDeleting(row.original)}
          >
            <Trash2 size={15} />
          </Button>
        </div>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title={kind === "kafka" ? "Kafka clusters" : "Kafka Connect clusters"}
        description={
          kind === "kafka"
            ? "Register the streaming infrastructure that carries your capture events."
            : "Register worker clusters and associate them with Kafka infrastructure."
        }
        eyebrow={kind === "kafka" ? "STREAM" : "CAPTURE RUNTIME"}
      >
        <Button onClick={() => edit(null)}>
          <Plus size={16} />
          Register cluster
        </Button>
      </PageHeader>
      {query.data && (
        <InventoryStrip
          items={[
            { label: "Registered clusters", value: query.data.length },
            {
              label: "Healthy",
              value: query.data.filter((c) => c.status === "HEALTHY").length,
            },
            {
              label: "Unhealthy",
              value: query.data.filter((c) => c.status === "UNHEALTHY").length,
            },
            {
              label: "Unverified",
              value: query.data.filter((c) => c.status === "UNKNOWN").length,
            },
          ]}
        />
      )}
      <div className="info-strip">
        Use endpoints reachable from the API container.{" "}
        {kind === "kafka"
          ? "The local broker is kafka:29092."
          : "The local worker endpoint is http://kafka-connect:8083."}
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} />
      ) : query.data.length ? (
        <DataTable data={query.data} columns={columns} />
      ) : (
        <Empty
          title="Register your infrastructure"
          description="Use the Kafka and Connect services bundled in Docker Compose to get started."
        />
      )}
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={`${editing ? "Edit" : "Register"} ${kind === "kafka" ? "Kafka" : "Kafka Connect"} cluster`}
        description="ClueCDC reaches infrastructure through server-side adapters."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
          className="form-grid"
        >
          <Field label="Cluster name">
            <Input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field
            label={kind === "kafka" ? "Bootstrap servers" : "REST endpoint"}
          >
            <Input
              required
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
          </Field>
          {kind === "kafka" ? (
            <Field label="Security protocol">
              <select
                value={protocol}
                onChange={(e) => setProtocol(e.target.value)}
              >
                <option>PLAINTEXT</option>
                <option>SSL</option>
              </select>
            </Field>
          ) : (
            <Field label="Kafka cluster">
              <select
                required
                value={kafkaId}
                onChange={(e) => setKafkaId(e.target.value)}
              >
                <option value="">Select Kafka cluster</option>
                {kafka.data?.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {save.isError && (
            <div className="full-width">
              <ErrorPanel error={save.error} />
            </div>
          )}
          <div className="dialog-actions full-width">
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button disabled={save.isPending}>Save cluster</Button>
          </div>
        </form>
      </Dialog>
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title="Delete cluster?"
        description="The cluster registration will be deleted. Associated pipelines and worker registrations must be removed first."
      >
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setDeleting(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Delete
          </Button>
        </div>
        {remove.isError && <ErrorPanel error={remove.error} />}
      </Dialog>
    </>
  );
}

export function ConnectorsPage() {
  const [selectedRecord, setSelected] = useState<Connector | null>(null);
  const [dismissedName, setDismissedName] = useState("");
  const [direction, setDirection] = useState("");
  const [state, setState] = useState("");
  const params = useSearchParams();
  const requestedName = params.get("connector");
  const query = useQuery({
    queryKey: ["connectors"],
    queryFn: () => api<Connector[]>("/connect/connectors"),
    refetchInterval: 10000,
  });
  const selected =
    selectedRecord ||
    (requestedName !== dismissedName
      ? query.data?.find((c) => c.name === requestedName)
      : null);
  const runtime = useQuery({
    queryKey: ["connector-runtime", selected?.id],
    queryFn: () =>
      api<Runtime>(
        `/connect/connectors/${encodeURIComponent(selected?.name || "")}/status?cluster_id=${selected?.connect_cluster_id}`,
      ),
    enabled: !!selected,
  });
  return (
    <>
      <PageHeader
        title="Connectors"
        description="Inspect source and sink runtimes behind capture pipelines and destinations."
        eyebrow="PLATFORM / CONNECT"
      />
      <FilterBar>
        <select
          aria-label="Connector direction"
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
        >
          <option value="">All directions</option>
          <option value="source">Source</option>
          <option value="sink">Sink</option>
        </select>
        <select
          aria-label="Connector state"
          value={state}
          onChange={(e) => setState(e.target.value)}
        >
          <option value="">All states</option>
          {["RUNNING", "FAILED", "DEGRADED", "PAUSED", "UNKNOWN"].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
      </FilterBar>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} />
      ) : query.data.length ? (
        <DataTable
          onRowClick={(c) => setSelected(c)}
          data={query.data.filter(
            (c) =>
              (!direction || c.connector_type === direction) &&
              (!state || c.actual_state === state),
          )}
          columns={[
            {
              accessorKey: "name",
              header: "Connector",
              cell: ({ row }) => (
                <button
                  className="text-link"
                  onClick={() => setSelected(row.original)}
                >
                  {row.original.name}
                </button>
              ),
            },
            { accessorKey: "connector_class", header: "Class" },
            {
              accessorKey: "connector_type",
              header: "Direction",
              cell: ({ row }) => row.original.connector_type.toUpperCase(),
            },
            {
              id: "tasks",
              header: "Tasks",
              cell: ({ row }) => {
                const value = row.original.runtime_json as {
                  tasks?: { state: string }[];
                };
                const tasks = value?.tasks;
                return tasks
                  ? `${tasks.filter((task) => task.state === "RUNNING").length}/${tasks.length}`
                  : "Unavailable";
              },
            },
            {
              id: "resource",
              header: "Related resource",
              cell: ({ row }) =>
                row.original.related_resource ? (
                  <Link
                    className="text-link"
                    href={row.original.related_resource.href}
                  >
                    {row.original.related_resource.name}
                  </Link>
                ) : (
                  "Unavailable"
                ),
            },
            {
              accessorKey: "desired_state",
              header: "Desired",
              cell: ({ row }) => <Status value={row.original.desired_state} />,
            },
            {
              accessorKey: "actual_state",
              header: "Actual",
              cell: ({ row }) => <Status value={row.original.actual_state} />,
            },
          ]}
        />
      ) : (
        <Empty
          title="No managed connectors"
          description="Create and deploy a pipeline to register a Debezium capture connector."
          href="/pipelines/new"
          action="Create pipeline"
        />
      )}
      <DetailDrawer
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) {
            setSelected(null);
            setDismissedName(requestedName || "");
          }
        }}
        title="Runtime connector state"
        description={selected?.name}
      >
        {runtime.isPending ? (
          <Loading />
        ) : runtime.isError ? (
          <ErrorPanel error={runtime.error} />
        ) : (
          <JsonView value={runtime.data} />
        )}
        <p>
          <Link className="text-link" href="/pipelines">
            Manage capture lifecycle in Pipelines →
          </Link>
        </p>
        {selected?.related_resource && (
          <Link className="text-link" href={selected.related_resource.href}>
            Open {selected.related_resource.name} →
          </Link>
        )}
      </DetailDrawer>
    </>
  );
}
