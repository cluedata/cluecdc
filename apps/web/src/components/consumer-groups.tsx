"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import type {
  ConsumerGroup,
  ConsumerGroupOffset,
  ConsumerGroupReport,
} from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { api, date } from "@/lib/api";
import {
  DataTable,
  Empty,
  ErrorPanel,
  Loading,
  PageHeader,
  Status,
} from "./common";
import { FilterBar, InventoryStrip } from "./operational";

type OffsetRow = ConsumerGroupOffset &
  Pick<
    ConsumerGroup,
    | "group_id"
    | "cluster_id"
    | "cluster_name"
    | "state"
    | "members"
    | "resource_id"
    | "resource_name"
    | "connector_name"
  >;

export function ConsumerGroupsPage() {
  const [search, setSearch] = useState("");
  const [cluster, setCluster] = useState("");
  const [state, setState] = useState("");
  const query = useQuery({
    queryKey: ["consumer-groups"],
    queryFn: () => api<ConsumerGroupReport>("/kafka/consumer-groups"),
    refetchInterval: 15000,
  });
  const rows = useMemo<OffsetRow[]>(
    () =>
      (query.data?.groups || []).flatMap((group) =>
        group.offsets.map((offset) => ({
          ...offset,
          group_id: group.group_id,
          cluster_id: group.cluster_id,
          cluster_name: group.cluster_name,
          state: group.state,
          members: group.members,
          resource_id: group.resource_id,
          resource_name: group.resource_name,
          connector_name: group.connector_name,
        })),
      ),
    [query.data],
  );
  const filtered = rows.filter((row) => {
    const text =
      `${row.group_id} ${row.topic} ${row.resource_name || ""}`.toLowerCase();
    return (
      (!search || text.includes(search.toLowerCase())) &&
      (!cluster || row.cluster_id === cluster) &&
      (!state || row.state === state)
    );
  });
  const groups = query.data?.groups || [];
  const states = Array.from(new Set(groups.map((group) => group.state))).sort();
  const totalLag = groups.reduce((total, group) => total + group.total_lag, 0);

  return (
    <>
      <PageHeader
        title="Consumer Groups"
        description="Committed offsets and broker lag for workloads that consume Kafka topics."
        eyebrow="INFRASTRUCTURE / KAFKA"
      >
        {query.data && (
          <span className="muted" title={date(query.data.observed_at)}>
            Observed {date(query.data.observed_at)}
          </span>
        )}
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCw size={15} />
          Refresh
        </Button>
      </PageHeader>

      {query.data && (
        <InventoryStrip
          items={[
            { label: "Consumer groups", value: groups.length },
            {
              label: "Active members",
              value: groups.reduce((total, group) => total + group.members, 0),
            },
            { label: "Tracked partitions", value: rows.length },
            { label: "Total lag", value: totalLag.toLocaleString() },
          ]}
        />
      )}

      {query.data?.clusters.some((item) => item.error) && (
        <div className="consumer-cluster-warnings" role="status">
          {query.data.clusters
            .filter((item) => item.error)
            .map((item) => (
              <span key={item.id}>
                <strong>{item.name}</strong>: {item.error?.message}
              </span>
            ))}
        </div>
      )}

      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : !query.data.clusters.length ? (
        <Empty
          title="No Kafka clusters registered"
          description="Register a Kafka cluster before inspecting consumer offsets."
          href="/kafka/clusters"
          action="Register Kafka cluster"
        />
      ) : !rows.length ? (
        <Empty
          title="No committed consumer offsets"
          description="Deploy a delivery and let it consume records. Its consumer group will then appear here."
          href="/deliveries/new"
          action="Create delivery"
        />
      ) : (
        <>
          <FilterBar>
            <Input
              aria-label="Search consumer groups"
              placeholder="Search group, delivery, or topic"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <select
              aria-label="Kafka cluster"
              value={cluster}
              onChange={(event) => setCluster(event.target.value)}
            >
              <option value="">All clusters</option>
              {query.data.clusters.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Consumer group state"
              value={state}
              onChange={(event) => setState(event.target.value)}
            >
              <option value="">All states</option>
              {states.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
            <span className="pipeline-result-count">
              {filtered.length} of {rows.length} partitions
            </span>
          </FilterBar>
          {filtered.length ? (
            <DataTable
              search={false}
              data={filtered}
              columns={[
                {
                  accessorKey: "group_id",
                  header: "Consumer group",
                  cell: ({ row }) => (
                    <span className="consumer-group-cell">
                      <code>{row.original.group_id}</code>
                      {row.original.resource_id ? (
                        <Link
                          className="text-link"
                          href={`/deliveries/${row.original.resource_id}`}
                        >
                          {row.original.resource_name || "Open delivery"}
                        </Link>
                      ) : row.original.connector_name ? (
                        <small>{row.original.connector_name}</small>
                      ) : null}
                    </span>
                  ),
                },
                { accessorKey: "cluster_name", header: "Cluster" },
                {
                  accessorKey: "state",
                  header: "State",
                  cell: ({ row }) => <Status value={row.original.state} />,
                },
                {
                  accessorKey: "topic",
                  header: "Topic",
                  cell: ({ row }) => <code>{row.original.topic}</code>,
                },
                { accessorKey: "partition", header: "Partition" },
                {
                  accessorKey: "committed_offset",
                  header: "Committed offset",
                  cell: ({ row }) =>
                    row.original.committed_offset.toLocaleString(),
                },
                {
                  accessorKey: "end_offset",
                  header: "End offset",
                  cell: ({ row }) =>
                    row.original.end_offset === null
                      ? "Unavailable"
                      : row.original.end_offset.toLocaleString(),
                },
                {
                  accessorKey: "lag",
                  header: "Lag",
                  cell: ({ row }) => (
                    <strong className={row.original.lag ? "red" : "green"}>
                      {row.original.lag === null
                        ? "Unavailable"
                        : row.original.lag.toLocaleString()}
                    </strong>
                  ),
                },
                { accessorKey: "members", header: "Members" },
              ]}
            />
          ) : (
            <Empty
              title="No offsets match these filters"
              description="Adjust the group, cluster, or state filter."
            />
          )}
        </>
      )}
    </>
  );
}
