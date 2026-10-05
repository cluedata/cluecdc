"use client";

import {
  DataTable,
  Empty,
  ErrorPanel,
  Loading,
  PageHeader,
} from "@/components/common";
import { FilterBar, InventoryStrip } from "@/components/operational";
import { api } from "@/lib/api";
import type { Pipeline, Source } from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { pipelineColumns } from "./shared";

export function PipelinesPage() {
  const [sourceFilter, setSourceFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const params = useSearchParams();
  const [search, setSearch] = useState(params.get("q") || "");
  const sourceMetadata = useQuery({
    queryKey: ["sources"],
    queryFn: () => api<Source[]>("/sources"),
  });
  const query = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
    refetchInterval: 10000,
  });
  const filtered = (query.data || []).filter((pipeline) => {
    const haystack = [
      pipeline.name,
      pipeline.source_name,
      pipeline.kafka_name,
      ...(pipeline.topics || []),
      ...(pipeline.deliveries_summary || []).flatMap((delivery) => [
        delivery.name,
        delivery.destination_name,
      ]),
    ]
      .join(" ")
      .toLowerCase();
    return (
      (!search || haystack.includes(search.toLowerCase())) &&
      (!sourceFilter || pipeline.source_id === sourceFilter) &&
      (!stateFilter || pipeline.aggregate_status === stateFilter)
    );
  });
  return (
    <>
      <PageHeader
        title="Pipelines"
        description="Browse every capture pipeline. Open a row to inspect its flow, tables, runtime, and operations."
        eyebrow="DATA FLOW"
      >
        <Button asChild>
          <Link href="/pipelines/new">
            <Plus size={16} />
            Create pipeline
          </Link>
        </Button>
      </PageHeader>
      {query.data && (
        <InventoryStrip
          items={[
            { label: "Pipelines", value: query.data.length },
            {
              label: "Healthy",
              value: query.data.filter((p) => p.aggregate_status === "HEALTHY")
                .length,
            },
            {
              label: "Paused",
              value: query.data.filter((p) => p.aggregate_status === "PAUSED")
                .length,
            },
            {
              label: "Require attention",
              value: query.data.filter((p) =>
                ["FAILED", "DEGRADED"].includes(p.aggregate_status || ""),
              ).length,
            },
          ]}
        />
      )}
      <FilterBar>
        <Input
          aria-label="Search pipelines"
          placeholder="Search name, source, destination, topic or table"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select
          aria-label="Pipeline source"
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
        >
          <option value="">All sources</option>
          {sourceMetadata.data?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Pipeline health"
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
        >
          <option value="">All health states</option>
          {[
            "HEALTHY",
            "DEGRADED",
            "FAILED",
            "PAUSED",
            "CREATING",
            "UPDATING",
            "UNKNOWN",
          ].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <span className="pipeline-result-count">
          {filtered.length} of {query.data?.length || 0} pipelines
        </span>
      </FilterBar>
      {sourceMetadata.isError && (
        <ErrorPanel
          error={sourceMetadata.error}
          retry={() => sourceMetadata.refetch()}
        />
      )}
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : query.data.length ? (
        filtered.length ? (
          <DataTable
            data={filtered}
            columns={pipelineColumns}
            search={false}
            getRowHref={(pipeline) => `/pipelines/${pipeline.id}`}
          />
        ) : (
          <Empty
            title="No pipelines match these filters"
            description="Adjust the search, source, or health filter."
          />
        )
      ) : (
        <Empty
          title="No pipelines yet"
          description="Create a pipeline to move data from your source database through Kafka to a destination."
          href="/pipelines/new"
          action="Create pipeline"
        />
      )}
    </>
  );
}
