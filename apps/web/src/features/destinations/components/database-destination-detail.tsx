"use client";

import {
  DataTable,
  Empty,
  ErrorPanel,
  Field,
  Loading,
  PageHeader,
  Status,
  Tabs,
} from "@/components/common";
import { api, date, post } from "@/lib/api";
import { destinationSchema, type DestinationForm } from "@/lib/validation";
import type {
  Audit,
  Delivery,
  Destination,
  DestinationRuntime,
  TopicMapping,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import {
  ConnectionFields,
  databaseName,
  initialConnection,
  Unavailable,
} from "./shared";

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
