"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  ArrowLeft,
  ArrowRight,
  DatabaseZap,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import type { Audit, Delivery } from "@cluecdc/contracts";
import { Button, Dialog } from "@cluecdc/ui";
import { api, date, number } from "@/lib/api";
import { connectorTechnology } from "@/domain/data-flow";
import { deliveryKeys, deliveryService } from "@/services/delivery-service";
import {
  DataTable,
  Empty,
  ErrorPanel,
  JsonView,
  Loading,
  PageHeader,
  Status,
  Tabs,
} from "./common";
import { DataFlowRail } from "./data-flow-rail";
import { FilterBar, MetricCard, Panel, QuietState } from "./operational";
import { DestinationWizard } from "./destinations";
import { useAuthorization } from "@/lib/auth";

export function DeliveryCreatePage() {
  return (
    <>
      <div className="wizard-actions">
        <Button asChild variant="outline">
          <Link href="/deliveries/new/object-storage">
            Create S3 / MinIO delivery
          </Link>
        </Button>
      </div>
      <DestinationWizard deliveryFirst />
    </>
  );
}

function deliveryType(delivery: Delivery): string {
  if (delivery.delivery_type === "OBJECT_STORAGE")
    return "Object storage (JSONL)";
  return connectorTechnology(delivery.connector).replace("Kafka Connect ", "");
}

function destinationHref(delivery: Delivery): string {
  if (delivery.delivery_type === "OBJECT_STORAGE")
    return `/connections/${delivery.destination_id}`;
  return `/destinations/${delivery.destination_id}`;
}

function DestinationReference({ delivery }: { delivery: Delivery }) {
  const { can } = useAuthorization();
  return can("destinations.read") ? (
    <Link className="text-link" href={destinationHref(delivery)}>
      {delivery.destination.name}
    </Link>
  ) : (
    <>{delivery.destination.name}</>
  );
}

function destinationDetail(delivery: Delivery): string {
  if (delivery.delivery_type === "OBJECT_STORAGE")
    return `${delivery.destination.type} / JSONL archive`;
  return `${delivery.destination.type} / ${delivery.destination.database_name}`;
}

function taskSummary(delivery: Delivery): string {
  const tasks = delivery.tasks || [];
  if (!tasks.length) return "Unavailable";
  return `${tasks.filter((task) => task.state === "RUNNING").length}/${tasks.length}`;
}

const columns: ColumnDef<Delivery>[] = [
  {
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => (
      <Link className="entity-link" href={`/deliveries/${row.original.id}`}>
        <span className="table-icon">
          <DatabaseZap size={16} />
        </span>
        <div>
          {row.original.name}
          <small>{row.original.connector?.name || "Not deployed"}</small>
        </div>
      </Link>
    ),
  },
  { id: "type", header: "Type", accessorFn: deliveryType },
  {
    id: "input",
    header: "Input",
    cell: ({ row }) => `${row.original.topic_mapping_json.length} topics`,
  },
  {
    id: "destination",
    header: "Destination",
    cell: ({ row }) => <DestinationReference delivery={row.original} />,
  },
  {
    id: "connect",
    header: "Kafka Connect",
    accessorFn: (delivery) => delivery.connect_cluster?.name || "Unavailable",
  },
  { id: "tasks", header: "Tasks", accessorFn: taskSummary },
  {
    accessorKey: "actual_state",
    header: "Status",
    cell: ({ row }) => <Status value={row.original.actual_state} />,
  },
  {
    id: "lag",
    header: "Lag",
    cell: ({ row }) => (
      <span className="muted">
        {row.original.metrics?.lag == null
          ? "Unavailable"
          : `${number(row.original.metrics.lag)}ms`}
      </span>
    ),
  },
  {
    accessorKey: "updated_at",
    header: "Updated",
    cell: ({ row }) => date(row.original.updated_at),
  },
  {
    id: "open",
    header: "",
    cell: ({ row }) => (
      <Link
        href={`/deliveries/${row.original.id}`}
        aria-label={`Open ${row.original.name}`}
      >
        <ArrowRight size={16} />
      </Link>
    ),
  },
];

export function DeliveriesPage() {
  const { can } = useAuthorization();
  const [state, setState] = useState("");
  const params = useSearchParams();
  const search = (params.get("q") || "").toLowerCase();
  const query = useQuery({
    queryKey: deliveryKeys.all,
    queryFn: deliveryService.list,
    refetchInterval: 10000,
  });
  const filtered = useMemo(
    () =>
      (query.data || []).filter((delivery) => {
        const haystack = [
          delivery.name,
          delivery.pipeline_name,
          delivery.destination.name,
          delivery.connect_cluster?.name,
          ...delivery.topic_mapping_json.flatMap((mapping) => [
            mapping.topic,
            `${mapping.schema_name}.${mapping.table_name}`,
          ]),
        ]
          .join(" ")
          .toLowerCase();
        return (
          (!state || delivery.actual_state === state) &&
          (!search || haystack.includes(search))
        );
      }),
    [query.data, search, state],
  );
  return (
    <>
      <PageHeader
        title="Deliveries"
        description="Move Kafka topics to destination endpoints with managed sink connectors."
        eyebrow="COMPONENTS / DELIVERY"
      >
        {can("deliveries.write") && (
          <Button asChild>
            <Link href="/deliveries/new">
              <Plus size={16} /> Create delivery
            </Link>
          </Button>
        )}
      </PageHeader>
      <FilterBar>
        <select
          aria-label="Delivery status"
          value={state}
          onChange={(event) => setState(event.target.value)}
        >
          <option value="">All</option>
          {["RUNNING", "DEGRADED", "FAILED", "PAUSED"].map((value) => (
            <option key={value} value={value}>
              {value[0] + value.slice(1).toLowerCase()}
            </option>
          ))}
        </select>
      </FilterBar>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : !query.data.length ? (
        <Empty
          title="No deliveries configured"
          description="A delivery moves data from Kafka topics to a destination."
          href={can("deliveries.write") ? "/deliveries/new" : undefined}
          action={can("deliveries.write") ? "Create delivery" : undefined}
        />
      ) : (
        <DataTable
          data={filtered}
          columns={columns}
          getRowHref={(delivery) => `/deliveries/${delivery.id}`}
          emptyTitle="No deliveries match these filters"
        />
      )}
    </>
  );
}

function TopicTable({ delivery }: { delivery: Delivery }) {
  const { role } = useAuthorization();
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Topic</th>
            <th>Partitions</th>
            <th>Consumer lag</th>
            <th>
              {delivery.delivery_type === "OBJECT_STORAGE"
                ? "Format"
                : "Destination table"}
            </th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {delivery.topic_mapping_json.map((mapping) => (
            <tr key={mapping.topic}>
              <td>
                {role === "Admin" ? (
                  <Link
                    className="text-link"
                    href={`/kafka/topics/${encodeURIComponent(mapping.topic)}`}
                  >
                    {mapping.topic}
                  </Link>
                ) : (
                  mapping.topic
                )}
              </td>
              <td>Unavailable</td>
              <td>
                {delivery.metrics?.lag == null
                  ? "Unavailable"
                  : `${delivery.metrics.lag}ms`}
              </td>
              <td>
                <code>
                  {delivery.delivery_type === "OBJECT_STORAGE"
                    ? "JSONL"
                    : `${mapping.schema_name}.${mapping.table_name}`}
                </code>
              </td>
              <td>
                <Status value={delivery.actual_state} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DeliveryDetailPage({ id }: { id: string }) {
  const { can } = useAuthorization();
  const canOperate = can("deliveries.operate");
  const canDelete = can("deliveries.admin");
  const canAudit = can("audit.read");
  const router = useRouter();
  const client = useQueryClient();
  const [tab, setTab] = useState("Overview");
  const [configurationMode, setConfigurationMode] = useState("Basic");
  const [deleting, setDeleting] = useState(false);
  const query = useQuery({
    queryKey: deliveryKeys.detail(id),
    queryFn: () => deliveryService.detail(id),
  });
  const runtime = useQuery({
    queryKey: deliveryKeys.status(id),
    queryFn: () => deliveryService.status(id),
    refetchInterval: 10000,
  });
  const history = useQuery({
    queryKey: ["delivery-history", id],
    queryFn: () => api<Audit[]>(`/audit?resource_id=${id}`),
    enabled: canAudit,
  });
  const operation = useMutation({
    mutationFn: (value: "pause" | "resume" | "restart") =>
      deliveryService.operate(id, value),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: deliveryKeys.detail(id) });
      await client.invalidateQueries({ queryKey: deliveryKeys.status(id) });
      await client.invalidateQueries({ queryKey: deliveryKeys.all });
    },
  });
  const remove = useMutation({
    mutationFn: () => deliveryService.remove(id),
    onSuccess: () => router.push("/deliveries"),
  });
  if (query.isPending) return <Loading />;
  if (query.isError)
    return <ErrorPanel error={query.error} retry={() => query.refetch()} />;
  const delivery = query.data;
  const actual = runtime.data?.actual_state || delivery.actual_state;
  const tasks = runtime.data?.tasks || delivery.tasks || [];
  const tabs = [
    "Overview",
    "Topics",
    ...(delivery.delivery_type === "OBJECT_STORAGE" ? [] : ["Mapping"]),
    "Configuration",
    "Tasks",
    "Metrics",
    "Logs",
    ...(canAudit ? ["History"] : []),
  ];
  return (
    <>
      <Link className="back-link" href="/deliveries">
        <ArrowLeft size={14} /> All deliveries
      </Link>
      <PageHeader
        title={delivery.name}
        description={`${deliveryType(delivery)} · ${delivery.pipeline_name} → ${delivery.destination.name}`}
        eyebrow="COMPONENTS / DELIVERY"
      >
        <Status value={actual} />
        {canOperate &&
          (actual === "PAUSED" ? (
            <Button
              variant="outline"
              disabled={operation.isPending}
              onClick={() => operation.mutate("resume")}
            >
              <Play size={14} /> Resume
            </Button>
          ) : (
            <Button
              variant="outline"
              disabled={operation.isPending}
              onClick={() => operation.mutate("pause")}
            >
              <Pause size={14} /> Pause
            </Button>
          ))}
        {canOperate && (
          <Button
            variant="outline"
            disabled={operation.isPending}
            onClick={() => operation.mutate("restart")}
          >
            <RefreshCw size={14} /> Restart
          </Button>
        )}
        {canDelete && (
          <Button
            variant="ghost"
            aria-label="Delete delivery"
            onClick={() => setDeleting(true)}
          >
            <Trash2 size={16} />
          </Button>
        )}
      </PageHeader>
      <Tabs tabs={tabs} active={tab} onChange={setTab} />
      {runtime.isError && (
        <ErrorPanel error={runtime.error} retry={() => runtime.refetch()} />
      )}
      {operation.isError && <ErrorPanel error={operation.error} />}
      {tab === "Overview" && (
        <>
          <section className="panel flow-panel">
            <div className="section-heading">
              <div>
                <h2>Delivery flow</h2>
                <p className="muted">
                  Kafka input, managed delivery runtime and destination
                  endpoint.
                </p>
              </div>
            </div>
            <DataFlowRail
              label={`${delivery.name} delivery flow`}
              lag={delivery.metrics?.lag}
              throughput={delivery.metrics?.throughput}
              stages={[
                {
                  label: "Stream",
                  name:
                    delivery.pipeline?.topic_prefix ||
                    `${delivery.topic_mapping_json.length} topics`,
                  detail: "Apache Kafka",
                  status: "HEALTHY",
                  href: `/pipelines/${delivery.pipeline_id}`,
                },
                {
                  label: "Delivery",
                  name: delivery.name,
                  detail: connectorTechnology(delivery.connector),
                  status: actual,
                  href: `/deliveries/${delivery.id}`,
                },
                {
                  label: "Destination",
                  name: delivery.destination.name,
                  detail: destinationDetail(delivery),
                  status: delivery.destination.status,
                  href: can("destinations.read")
                    ? destinationHref(delivery)
                    : undefined,
                },
              ]}
            />
          </section>
          <div className="metric-row compact">
            <MetricCard
              label="Kafka cluster"
              value={delivery.pipeline?.kafka_name || "See pipeline"}
              detail="Stream infrastructure"
              icon={DatabaseZap}
            />
            <MetricCard
              label="Kafka Connect"
              value={delivery.connect_cluster?.name || "Unavailable"}
              detail="Delivery runtime"
              icon={DatabaseZap}
            />
            <MetricCard
              label="Tasks"
              value={
                tasks.length
                  ? `${tasks.filter((task) => task.state === "RUNNING").length}/${tasks.length}`
                  : "Unavailable"
              }
              detail="Running / total"
              icon={DatabaseZap}
            />
            <MetricCard
              label="Lag"
              value={
                delivery.metrics?.lag == null
                  ? "Unavailable"
                  : `${delivery.metrics.lag}ms`
              }
              detail="Metrics provider required"
              icon={DatabaseZap}
              unavailable={delivery.metrics?.lag == null}
            />
          </div>
          <Panel title="Runtime details">
            <dl className="facts">
              <dt>Pipeline</dt>
              <dd>
                <Link
                  className="text-link"
                  href={`/pipelines/${delivery.pipeline_id}`}
                >
                  {delivery.pipeline_name}
                </Link>
              </dd>
              <dt>Connector name</dt>
              <dd>
                <code>{delivery.connector?.name || "Not deployed"}</code>
              </dd>
              <dt>Connector class</dt>
              <dd>
                <code>
                  {delivery.connector?.connector_class || "Unavailable"}
                </code>
              </dd>
              <dt>Destination</dt>
              <dd>
                <DestinationReference delivery={delivery} />
              </dd>
              <dt>Desired state</dt>
              <dd>{delivery.desired_state}</dd>
              <dt>Last restart</dt>
              <dd>Unavailable</dd>
            </dl>
          </Panel>
        </>
      )}
      {tab === "Topics" && <TopicTable delivery={delivery} />}
      {tab === "Mapping" && (
        <Panel
          title="Topic mapping"
          description="Human-readable routing from Kafka topics to destination tables."
        >
          <div className="mapping-list">
            {delivery.topic_mapping_json.map((mapping) => (
              <div key={mapping.topic}>
                <code>{mapping.topic}</code>
                <ArrowRight size={16} aria-hidden="true" />
                <code>
                  {mapping.schema_name}.{mapping.table_name}
                </code>
              </div>
            ))}
          </div>
          {can("deliveries.write") && can("destinations.read") && (
            <Button asChild variant="outline">
              <Link href={destinationHref(delivery)}>Edit mapping</Link>
            </Button>
          )}
        </Panel>
      )}
      {tab === "Configuration" && (
        <Panel title="Delivery settings" description="Powered by Kafka Connect">
          <Tabs
            tabs={["Basic", "Advanced"]}
            active={configurationMode}
            onChange={setConfigurationMode}
          />
          {configurationMode === "Basic" &&
          delivery.delivery_type === "OBJECT_STORAGE" ? (
            <dl className="facts">
              <dt>Format</dt>
              <dd>JSONL · Debezium event envelope</dd>
              <dt>Compression</dt>
              <dd>{delivery.configuration_json.compression}</dd>
              <dt>Records per file</dt>
              <dd>{delivery.configuration_json.file_max_records}</dd>
              <dt>Flush interval</dt>
              <dd>{delivery.configuration_json.flush_interval_ms} ms</dd>
              <dt>Object key template</dt>
              <dd>
                <code>{delivery.configuration_json.file_name_template}</code>
              </dd>
              <dt>Delete handling</dt>
              <dd>Delete envelopes are archived</dd>
            </dl>
          ) : configurationMode === "Basic" ? (
            <dl className="facts">
              <dt>Destination</dt>
              <dd>{delivery.destination.name}</dd>
              <dt>Topics</dt>
              <dd>
                {delivery.topic_mapping_json
                  .map((mapping) => mapping.topic)
                  .join(", ")}
              </dd>
              <dt>Insert mode</dt>
              <dd>{delivery.configuration_json.write_mode}</dd>
              <dt>Primary key mode</dt>
              <dd>{delivery.configuration_json.primary_key_mode}</dd>
              <dt>Delete enabled</dt>
              <dd>{String(delivery.configuration_json.delete_enabled)}</dd>
              <dt>Auto create</dt>
              <dd>{String(delivery.configuration_json.auto_create)}</dd>
              <dt>Auto evolve</dt>
              <dd>{String(delivery.configuration_json.auto_evolve)}</dd>
            </dl>
          ) : (
            <JsonView
              value={
                delivery.connector?.config_json || delivery.configuration_json
              }
            />
          )}
        </Panel>
      )}
      {tab === "Tasks" && (
        <Panel title="Tasks">
          {tasks.length ? (
            <div className="task-list">
              {tasks.map((task) => (
                <div key={task.id}>
                  <strong>Task {task.id}</strong>
                  <Status value={task.state} />
                  <span className="muted">
                    {task.worker_id || "Worker unavailable"}
                  </span>
                  {task.error && <p className="field-error">{task.error}</p>}
                </div>
              ))}
            </div>
          ) : (
            <QuietState
              title="Task status unavailable"
              description="Deploy the delivery and refresh runtime status to inspect tasks."
            />
          )}
        </Panel>
      )}
      {tab === "Metrics" && (
        <Panel title="Delivery metrics">
          <QuietState
            title="Metrics provider required"
            description={
              delivery.metrics?.notice ||
              "Lag, throughput and last-message time are not exposed by the configured backend."
            }
          />
        </Panel>
      )}
      {tab === "Logs" && (
        <Panel title="Delivery logs">
          <QuietState
            title="Worker logs are not connected"
            description="Use the Kafka Connect worker logs for this connector until a log provider is configured."
          />
        </Panel>
      )}
      {tab === "History" && (
        <Panel title="History">
          {history.isPending ? (
            <Loading />
          ) : history.isError ? (
            <ErrorPanel error={history.error} retry={() => history.refetch()} />
          ) : history.data.length ? (
            <div className="audit-list">
              {history.data.map((entry) => (
                <div key={entry.id}>
                  <Status value={entry.action.split(".").at(-1) || "UPDATED"} />
                  <strong>{entry.action.replaceAll("_", " ")}</strong>
                  <span>
                    {entry.actor} · {date(entry.created_at)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <QuietState
              title="No delivery history"
              description="Lifecycle and configuration changes will appear here."
            />
          )}
        </Panel>
      )}
      {canDelete && (
        <Dialog
          open={deleting}
          onOpenChange={setDeleting}
          title="Delete delivery?"
          description="The managed sink connector and delivery association will be removed. Kafka topics, captured data and the destination connection are retained."
        >
          <div className="dialog-actions">
            <Button variant="outline" onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              Delete delivery
            </Button>
          </div>
          {remove.isError && <ErrorPanel error={remove.error} />}
        </Dialog>
      )}
    </>
  );
}
