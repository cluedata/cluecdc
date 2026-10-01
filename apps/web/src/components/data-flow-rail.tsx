"use client";
import Link from "next/link";
import { useId } from "react";
import { ArrowUpRight } from "lucide-react";

export type FlowState = "healthy" | "active" | "warning" | "error" | "paused";
export type FlowStage = {
  label: string;
  name: string;
  detail?: string;
  status?: string | null;
  href?: string;
};

export function flowState(status?: string | null): FlowState {
  const value = status?.toUpperCase();
  if (
    [
      "FAILED",
      "ERROR",
      "CRITICAL",
      "UNHEALTHY",
      "UNAVAILABLE",
      "OPEN",
    ].includes(value || "")
  )
    return "error";
  if (["DEGRADED", "WARNING", "PENDING", "ACKNOWLEDGED"].includes(value || ""))
    return "warning";
  if (["RUNNING", "DEPLOYING", "SNAPSHOTTING"].includes(value || ""))
    return "active";
  if (["HEALTHY", "READY", "PASSED", "COMPLETED"].includes(value || ""))
    return "healthy";
  return "paused";
}

export function DataFlowRail({
  stages,
  throughput,
  lag,
  label = "Pipeline topology",
}: {
  stages: FlowStage[];
  throughput?: number | null;
  lag?: number | null;
  label?: string;
}) {
  const id = useId();
  return (
    <div className="data-flow-rail" role="group" aria-label={label}>
      <ol
        className="rail-stages"
        style={{
          gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))`,
        }}
      >
        {stages.map((stage, index) => {
          const state = flowState(stage.status);
          const next = flowState(stages[index + 1]?.status);
          const segment =
            state === "error" || next === "error"
              ? "error"
              : state === "warning" || next === "warning"
                ? "warning"
                : state === "paused" || next === "paused"
                  ? "paused"
                  : state;
          const contents = (
            <>
              <strong>{stage.name}</strong>
              {stage.href && <ArrowUpRight size={12} aria-hidden="true" />}
            </>
          );
          return (
            <li
              key={`${stage.label}-${index}`}
              className={`rail-stage rail-${state} rail-to-${segment}`}
            >
              <div className="rail-stage-label">
                <span className="rail-index">0{index + 1}</span>
                {stage.label}
              </div>
              <div className="rail-track">
                <span className="rail-point" aria-hidden="true" />
                {index < stages.length - 1 && (
                  <svg
                    className={`rail-segment rail-${segment}`}
                    width="100%"
                    height="20"
                    aria-hidden="true"
                  >
                    <defs>
                      <marker
                        id={`${id}-${index}`}
                        markerWidth="5"
                        markerHeight="5"
                        refX="4"
                        refY="2.5"
                        orient="auto"
                      >
                        <path d="M0 0 L5 2.5 L0 5" fill="currentColor" />
                      </marker>
                    </defs>
                    <line
                      x1="10"
                      y1="10"
                      x2="100%"
                      y2="10"
                      className="rail-line"
                      markerEnd={`url(#${id}-${index})`}
                    />
                    {(segment === "healthy" || segment === "active") && (
                      <line
                        x1="10"
                        y1="10"
                        x2="100%"
                        y2="10"
                        className="rail-flow"
                      />
                    )}
                  </svg>
                )}
              </div>
              {stage.href ? (
                <Link className="rail-stage-name" href={stage.href}>
                  {contents}
                </Link>
              ) : (
                <div className="rail-stage-name">{contents}</div>
              )}
              {stage.detail && (
                <span className="rail-stage-detail">{stage.detail}</span>
              )}
              <span className="rail-stage-status">
                {(stage.status || "UNKNOWN").replaceAll("_", " ")}
              </span>
            </li>
          );
        })}
      </ol>
      {(throughput !== undefined || lag !== undefined) && (
        <div className="rail-telemetry">
          <span>
            EVENT RATE{" "}
            <strong>
              {throughput == null
                ? "Unavailable"
                : `${new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(throughput)}/s`}
            </strong>
          </span>
          <span>
            CDC LAG{" "}
            <strong>
              {lag == null ? "Unavailable" : `${lag.toLocaleString()}ms`}
            </strong>
          </span>
          {(throughput == null || lag == null) && (
            <small>Metrics provider required for unavailable values</small>
          )}
        </div>
      )}
    </div>
  );
}
