"use client";

import { Status } from "@/components/common";
import type { Pipeline } from "@cluecdc/contracts";
import { ColumnDef } from "@tanstack/react-table";
import { ArrowRight, GitBranch } from "lucide-react";
import Link from "next/link";

export const pipelineColumns: ColumnDef<Pipeline>[] = [
  {
    accessorKey: "name",
    header: "Pipeline",
    cell: ({ row }) => (
      <Link className="entity-link" href={`/pipelines/${row.original.id}`}>
        <span className="table-icon">
          <GitBranch size={16} />
        </span>
        <div>
          {row.original.name}
          <small>{row.original.topic_prefix}</small>
        </div>
      </Link>
    ),
  },
  {
    accessorKey: "source_name",
    header: "Source",
    cell: ({ row }) => (
      <Link className="text-link" href={`/sources/${row.original.source_id}`}>
        {row.original.source_name || "Unavailable"}
      </Link>
    ),
  },
  {
    id: "coverage",
    header: "Coverage",
    accessorFn: (pipeline) => pipeline.topics?.length ?? pipeline.tables,
    cell: ({ row }) => (
      <span className="pipeline-coverage">
        <strong>{row.original.tables}</strong>
        <small>
          {row.original.tables === 1 ? "table" : "tables"} /{" "}
          {row.original.topics?.length ?? row.original.tables} topics
        </small>
      </span>
    ),
  },
  {
    accessorKey: "destinations",
    header: "Destinations",
    cell: ({ row }) => {
      const deliveries = row.original.deliveries_summary || [];
      return (
        <span className="pipeline-destinations">
          <strong>{row.original.destinations || deliveries.length || 0}</strong>
          <small>
            {deliveries.length
              ? deliveries
                  .map((delivery) => delivery.destination_name)
                  .join(", ")
              : "Not configured"}
          </small>
        </span>
      );
    },
  },
  {
    accessorKey: "aggregate_status",
    header: "Health",
    cell: ({ row }) => (
      <span title={row.original.status_reason}>
        <Status value={row.original.aggregate_status || "UNKNOWN"} />
      </span>
    ),
  },
  {
    accessorKey: "actual_state",
    header: "Capture",
    cell: ({ row }) => <Status value={row.original.actual_state} />,
  },
  {
    accessorKey: "delivery_state",
    header: "Delivery",
    cell: ({ row }) =>
      row.original.delivery_state === "NOT_CONFIGURED" ? (
        <span className="muted">Not configured</span>
      ) : (
        <Status value={row.original.delivery_state} />
      ),
  },
  {
    id: "open",
    header: "",
    cell: ({ row }) => (
      <Link
        href={`/pipelines/${row.original.id}`}
        aria-label={`Open ${row.original.name}`}
      >
        <ArrowRight size={16} />
      </Link>
    ),
  },
];
