"use client";

import {
  DataTable,
  Empty,
  ErrorPanel,
  Loading,
  PageHeader,
  Status,
} from "@/components/common";
import { api, date, post } from "@/lib/api";
import type { Connection, ConnectionCategory } from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Loader2, Pencil, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { categoryCopy, providerName } from "./shared";
import { useAuthorization } from "@/lib/auth";
import { ConnectionDialog } from "./connection-dialog";
import {
  connectionTestPassed,
  type ConnectionTestFeedback,
} from "@/lib/connection-test";
import {
  discoveredTableCount,
  loadSourceMetadata,
} from "@/lib/source-metadata";

export function ConnectionsPage({
  capability,
}: {
  capability?: "SOURCE" | "DESTINATION";
} = {}) {
  const { can } = useAuthorization();
  const writePermission =
    capability === "SOURCE" ? "sources.write" : "destinations.write";
  const adminPermission =
    capability === "SOURCE" ? "sources.admin" : "destinations.admin";
  const canWrite = can(writePermission);
  const canDelete = can(adminPermission);
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<ConnectionCategory | "ALL">("ALL");
  const [search, setSearch] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Connection | null>(null);
  const [deleting, setDeleting] = useState<Connection | null>(null);
  const connections = useQuery({
    queryKey: ["connections", capability],
    queryFn: () =>
      api<Connection[]>(
        `/connections${capability ? `?capability=${capability}` : ""}`,
      ),
  });
  const remove = useMutation({
    mutationFn: (connection: Connection) =>
      api(
        capability === "SOURCE"
          ? `/sources/${connection.id}`
          : capability === "DESTINATION"
            ? `/destinations/${connection.id}`
            : `/connections/${connection.id}`,
        { method: "DELETE" },
      ),
    onSuccess: () => {
      toast.success("Connection deleted");
      setDeleting(null);
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const test = useMutation({
    mutationFn: (connection: Connection) =>
      post<ConnectionTestFeedback>(
        capability === "SOURCE"
          ? `/sources/${connection.id}/test`
          : capability === "DESTINATION"
            ? `/destinations/${connection.id}/test`
            : `/connections/${connection.id}/test`,
      ),
    onSuccess: (result) => {
      if (connectionTestPassed(result)) {
        const count = result.checks?.length;
        toast.success(
          count
            ? `${count} connection checks passed`
            : "Connection test passed",
        );
      } else {
        toast.error("Connection test failed");
      }
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const discovery = useMutation({
    mutationFn: (connection: Connection) => loadSourceMetadata(connection.id),
    onSuccess: (job, connection) => {
      const count = discoveredTableCount(job);
      toast.success(
        count === null
          ? `Metadata loaded for ${connection.name}`
          : `${count} ${count === 1 ? "table" : "tables"} loaded for ${connection.name}`,
      );
      queryClient.invalidateQueries({
        queryKey: ["source-tables", connection.id],
      });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["source", connection.id] });
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const columns = useMemo<ColumnDef<Connection>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }) => (
          <div className="resource-name">
            <strong>{row.original.name}</strong>
            <small>{providerName(row.original.provider)}</small>
          </div>
        ),
      },
      {
        accessorKey: "type",
        header: "Type",
        cell: ({ row }) => providerName(row.original.type),
      },
      {
        accessorKey: "category",
        header: "Category",
        cell: ({ row }) => categoryCopy[row.original.category].title,
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row }) => <Status value={row.original.status} />,
      },
      {
        accessorKey: "used_by_count",
        header: "Used by",
        cell: ({ row }) => row.original.used_by_count || 0,
      },
      {
        accessorKey: "last_checked",
        header: "Last checked",
        cell: ({ row }) => date(row.original.last_checked),
      },
      {
        id: "actions",
        header: "Actions",
        cell: ({ row }) => (
          <div
            className="row-actions"
            onClick={(event) => event.stopPropagation()}
          >
            {canWrite && (
              <Button
                variant="ghost"
                disabled={test.isPending}
                onClick={() => test.mutate(row.original)}
              >
                Test
              </Button>
            )}
            {capability === "SOURCE" && canWrite && (
              <Button
                variant="ghost"
                aria-label={`Load metadata for ${row.original.name}`}
                disabled={discovery.isPending}
                onClick={() => discovery.mutate(row.original)}
              >
                {discovery.isPending &&
                discovery.variables?.id === row.original.id ? (
                  <Loader2 className="spin" size={15} />
                ) : (
                  <RefreshCw size={15} />
                )}
                Load metadata
              </Button>
            )}
            {canWrite && (
              <Button
                variant="ghost"
                aria-label={`Edit ${row.original.name}`}
                onClick={() => {
                  setEditing(row.original);
                  setEditorOpen(true);
                }}
              >
                <Pencil size={15} />
              </Button>
            )}
            <Button asChild variant="ghost">
              <Link
                href={
                  capability === "SOURCE"
                    ? `/sources/${row.original.id}`
                    : `/connections/${row.original.id}`
                }
              >
                Open
              </Link>
            </Button>
            {canDelete && (
              <Button
                variant="ghost"
                aria-label={`Delete ${row.original.name}`}
                disabled={remove.isPending}
                onClick={() => setDeleting(row.original)}
              >
                <Trash2 size={15} />
              </Button>
            )}
          </div>
        ),
      },
    ],
    [canDelete, canWrite, capability, discovery, remove, test],
  );
  if (connections.isPending) return <Loading />;
  if (connections.isError)
    return (
      <ErrorPanel
        error={connections.error}
        retry={() => connections.refetch()}
      />
    );
  const values = (connections.data || []).filter(
    (value) =>
      (category === "ALL" || value.category === category) &&
      (!search ||
        `${value.name} ${providerName(value.type)} ${value.category}`
          .toLowerCase()
          .includes(search.toLowerCase())),
  );
  const pageTitle = capability
    ? capability === "SOURCE"
      ? "Sources"
      : "Destinations"
    : "All Connections";
  return (
    <>
      <PageHeader
        title={pageTitle}
        description={
          capability
            ? `Filtered view of connections with the ${capability.toLowerCase()} capability.`
            : "Manage database and object storage connections."
        }
        eyebrow="CONNECTIONS"
      >
        {canWrite && (
          <Button
            onClick={() => {
              setEditing(null);
              setEditorOpen(true);
            }}
          >
            <Plus size={16} /> New Connection
          </Button>
        )}
      </PageHeader>
      <div className="filter-bar connection-filter-bar">
        <div className="tabs" role="tablist" aria-label="Connection category">
          {(
            ["ALL", ...(Object.keys(categoryCopy) as ConnectionCategory[])] as (
              ConnectionCategory | "ALL"
            )[]
          ).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={category === value}
              className={category === value ? "active" : ""}
              onClick={() => setCategory(value)}
            >
              {value === "ALL" ? "All" : categoryCopy[value].title}
            </button>
          ))}
        </div>
        <label className="search-field">
          <Search size={15} aria-hidden="true" />
          <Input
            aria-label="Search connections"
            placeholder="Search connections"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>
      {!capability && (
        <section
          className="connection-categories"
          aria-label="Connection categories"
        >
          {(Object.keys(categoryCopy) as ConnectionCategory[]).map(
            (category) => {
              const item = categoryCopy[category];
              const Icon = item.icon;
              return (
                <div className="connection-category" key={category}>
                  <Icon size={20} />
                  <div>
                    <strong>{item.title}</strong>
                    <p>{item.description}</p>
                  </div>
                  <span>
                    {
                      values.filter((value) => value.category === category)
                        .length
                    }
                  </span>
                </div>
              );
            },
          )}
        </section>
      )}
      {values.length ? (
        <DataTable
          data={values}
          columns={columns}
          getRowHref={(row) =>
            capability === "SOURCE"
              ? `/sources/${row.id}`
              : `/connections/${row.id}`
          }
        />
      ) : (
        <Empty
          title="No connections"
          description="Add a database or object storage connection."
          action={canWrite ? "Add connection" : undefined}
          onAction={
            canWrite
              ? () => {
                  setEditing(null);
                  setEditorOpen(true);
                }
              : undefined
          }
        />
      )}
      {canWrite && editorOpen && (
        <ConnectionDialog
          open={editorOpen}
          onOpenChange={setEditorOpen}
          value={editing}
          capability={capability}
        />
      )}
      {canDelete && (
        <Dialog
          open={!!deleting}
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete connection?"
          description="This is blocked while pipelines or deliveries use the connection. Stored credentials will also be removed."
        >
          {deleting?.used_by?.length ? (
            <div className="error-panel" role="alert">
              <strong>Cannot delete connection</strong>
              <p>This connection is currently used by:</p>
              <ul>
                {deleting.used_by.map((dependency) => (
                  <li key={`${dependency.type}-${dependency.id}`}>
                    {dependency.type === "pipeline" ? "Pipeline" : "Delivery"}:{" "}
                    {dependency.name}
                    {dependency.pipeline ? ` (${dependency.pipeline})` : ""}
                  </li>
                ))}
              </ul>
              <p>
                Remove the dependent resources before deleting this connection.
              </p>
            </div>
          ) : null}
          <div className="dialog-actions">
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending || !!deleting?.used_by?.length}
              onClick={() => deleting && remove.mutate(deleting)}
            >
              Delete connection
            </Button>
          </div>
          {remove.isError && <ErrorPanel error={remove.error} />}
        </Dialog>
      )}
    </>
  );
}
