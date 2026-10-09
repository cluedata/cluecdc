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
import type {
  Connection,
  ConnectionCategory,
  ConnectionTestResult,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { categoryCopy, providerName } from "./shared";
import { useAuthorization } from "@/lib/auth";

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
  const [deleting, setDeleting] = useState<Connection | null>(null);
  const connections = useQuery({
    queryKey: ["connections", capability],
    queryFn: () =>
      api<Connection[]>(
        `/connections${capability ? `?capability=${capability}` : ""}`,
      ),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/connections/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Connection deleted");
      setDeleting(null);
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const test = useMutation({
    mutationFn: (id: string) =>
      post<ConnectionTestResult>(`/connections/${id}/test`),
    onSuccess: (result) => {
      toast.success(`${result.checks.length} connection checks passed`);
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
                onClick={() => test.mutate(row.original.id)}
              >
                Test
              </Button>
            )}
            <Button asChild variant="ghost">
              <Link href={`/connections/${row.original.id}`}>Open</Link>
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
    [canDelete, canWrite, remove, test],
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
          <Button asChild>
            <Link href="/connections/new">
              <Plus size={16} /> New Connection
            </Link>
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
          getRowHref={(row) => `/connections/${row.id}`}
        />
      ) : (
        <Empty
          title="No connections"
          description="Add a database or object storage connection."
          href={canWrite ? "/connections/new" : undefined}
          action={canWrite ? "Add connection" : undefined}
        />
      )}
      {canDelete && (
        <Dialog
          open={!!deleting}
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete connection?"
          description="This is blocked while pipelines or deliveries use the connection. Stored credentials will also be removed."
        >
          <div className="dialog-actions">
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => deleting && remove.mutate(deleting.id)}
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
