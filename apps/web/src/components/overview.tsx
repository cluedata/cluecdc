"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  ChartNoAxesCombined,
  GitBranch,
  Plus,
  Radio,
  ShieldCheck,
  Timer,
} from "lucide-react";
import type {
  Audit,
  Json,
  OperationalError,
  Overview,
} from "@cluecdc/contracts";
import { Button } from "@cluecdc/ui";
import { api, date, number, relativeTime } from "@/lib/api";
import { useAuthorization } from "@/lib/auth";
import { ErrorPanel, Loading, PageHeader, Status } from "./common";
import {
  HealthIndicator,
  Panel,
  QuietState,
  StatusText,
  TimeRangeSelector,
  Tooltip,
} from "./operational";

function actionLabel(action: string) {
  return action
    .replaceAll("_", " ")
    .replaceAll(".", " ")
    .replace(/\b\w/g, (value) => value.toUpperCase());
}

export function changeClassification(diff: Json[]) {
  const classes = diff.flatMap((value) =>
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof value.classification === "string"
      ? [value.classification]
      : [],
  );
  return classes.includes("BREAKING")
    ? "BREAKING"
    : classes.includes("POTENTIALLY_BREAKING")
      ? "POTENTIALLY_BREAKING"
      : classes.length
        ? "NON_BREAKING"
        : "UNKNOWN";
}

export function MetricsHistory() {
  return (
    <Panel title="Throughput & lag" actions={<TimeRangeSelector />}>
      <div className="metrics-legend">
        <span>
          <Radio size={12} /> Events/sec
        </span>
        <span>
          <Timer size={12} /> Average CDC lag
        </span>
        <Tooltip text="Historical throughput, lag and event freshness require a metrics provider. No event samples are consumed by this dashboard." />
      </div>
      <QuietState
        icon={ChartNoAxesCombined}
        title="No historical metrics available"
        description="Configure a metrics provider to observe throughput and CDC lag over time."
      />
    </Panel>
  );
}

type HealthSegment = {
  label: string;
  value: number;
  tone: "healthy" | "degraded" | "failed" | "paused" | "unknown";
};

function HealthDistribution({
  overview,
  canCreate,
}: {
  overview: Overview;
  canCreate: boolean;
}) {
  const segments: HealthSegment[] = [
    { label: "Running", value: overview.running, tone: "healthy" },
    { label: "Degraded", value: overview.degraded, tone: "degraded" },
    { label: "Failed", value: overview.failed, tone: "failed" },
    { label: "Paused", value: overview.paused, tone: "paused" },
    { label: "Unknown", value: overview.unknown, tone: "unknown" },
  ];
  const total = Math.max(overview.pipelines, 0);

  if (!total) {
    return (
      <div className="data-flow-empty">
        <div className="flow-language" aria-hidden="true">
          <span>Source</span>
          <i>→</i>
          <span>Capture</span>
          <i>→</i>
          <span>Stream</span>
          <i>→</i>
          <span>Deliver</span>
          <i>→</i>
          <span>Observe</span>
        </div>
        <div>
          <GitBranch size={18} aria-hidden="true" />
          <span>
            <strong>No pipelines configured</strong>
            <small>Create a pipeline to begin tracking data movement.</small>
          </span>
          {canCreate && (
            <Button asChild>
              <Link href="/pipelines/new">Create pipeline</Link>
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="health-distribution">
      <div
        className="health-stack"
        role="img"
        aria-label={segments
          .map((segment) => `${segment.label}: ${segment.value}`)
          .join(", ")}
      >
        {segments
          .filter((segment) => segment.value > 0)
          .map((segment) => (
            <span
              key={segment.label}
              className={`health-segment health-${segment.tone}`}
              style={{ width: `${(segment.value / total) * 100}%` }}
              title={`${segment.label}: ${segment.value}`}
            />
          ))}
      </div>
      <dl className="health-legend">
        {segments.map((segment) => (
          <div key={segment.label}>
            <dt>
              <span className={`health-key health-${segment.tone}`} />
              {segment.label}
            </dt>
            <dd>{segment.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function InfrastructureRow({
  label,
  healthy,
  total,
  href,
}: {
  label: string;
  healthy: number;
  total: number;
  href: string;
}) {
  return (
    <Link className="infrastructure-row" href={href}>
      <strong>{label}</strong>
      <HealthIndicator healthy={healthy} total={total} />
    </Link>
  );
}

function OperationalSummary({
  overview,
  isAdmin,
}: {
  overview: Overview;
  isAdmin: boolean;
}) {
  const infrastructureTotal =
    overview.kafka_clusters + overview.connect_clusters;
  const infrastructureHealthy =
    overview.healthy_kafka_clusters + overview.healthy_connect_clusters;
  const items = [
    {
      label: "Pipelines",
      value: `${overview.running} active`,
      detail: overview.pipelines
        ? `${overview.pipelines} configured`
        : "No pipelines",
      href: "/pipelines",
      state: !overview.pipelines
        ? "NOT CONFIGURED"
        : overview.failed || overview.degraded
          ? "DEGRADED"
          : "HEALTHY",
    },
    {
      label: "Deliveries",
      value: `${overview.delivery_running} running`,
      detail: overview.deliveries
        ? `${overview.deliveries} configured`
        : "None configured",
      href: "/deliveries",
      state: !overview.deliveries
        ? "NOT CONFIGURED"
        : overview.delivery_failed || overview.delivery_degraded
          ? "DEGRADED"
          : "HEALTHY",
    },
    ...(isAdmin
      ? [
          {
            label: "Infrastructure",
            value: `${infrastructureHealthy} / ${infrastructureTotal} healthy`,
            detail:
              infrastructureTotal &&
              infrastructureHealthy === infrastructureTotal
                ? "All systems OK"
                : infrastructureTotal
                  ? "Review system health"
                  : "Not configured",
            href: "/monitoring",
            state: !infrastructureTotal
              ? "NOT CONFIGURED"
              : infrastructureHealthy === infrastructureTotal
                ? "HEALTHY"
                : "DEGRADED",
          },
          {
            label: "Issues",
            value: String(overview.errors),
            detail: overview.errors
              ? "Requires attention"
              : "No active incidents",
            href: "/operations/errors",
            state: overview.errors ? "ERROR" : "HEALTHY",
          },
        ]
      : []),
  ];
  return (
    <section
      className="operational-summary"
      aria-labelledby="operational-summary-title"
    >
      <h2 id="operational-summary-title">Operational summary</h2>
      <div>
        {items.map((item) => (
          <Link href={item.href} key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.detail}</small>
            <StatusText value={item.state} />
          </Link>
        ))}
      </div>
    </section>
  );
}

function collapseActivity(entries: Audit[]) {
  return entries.reduce<Array<{ entry: Audit; count: number }>>(
    (groups, entry) => {
      const previous = groups.at(-1);
      if (
        previous &&
        previous.entry.action === entry.action &&
        previous.entry.actor === entry.actor &&
        previous.entry.resource_type === entry.resource_type
      ) {
        previous.count += 1;
      } else {
        groups.push({ entry, count: 1 });
      }
      return groups;
    },
    [],
  );
}

function prioritizeIssues(entries: OperationalError[]) {
  const priority: Record<string, number> = {
    CRITICAL: 0,
    ERROR: 1,
    WARNING: 2,
    INFO: 3,
  };
  return [...entries].sort(
    (left, right) =>
      (priority[left.severity] ?? 4) - (priority[right.severity] ?? 4),
  );
}

export function OverviewPage() {
  const { can, role } = useAuthorization();
  const isAdmin = role === "Admin";
  const interval = 15000;
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: () => api<Overview>("/monitoring/overview"),
    refetchInterval: interval,
  });
  const errors = useQuery({
    queryKey: ["errors", "recent-active"],
    queryFn: () =>
      api<OperationalError[]>("/operations/errors?limit=5&active=true"),
    refetchInterval: interval,
    enabled: isAdmin,
  });
  const activity = useQuery({
    queryKey: ["audit", "meaningful"],
    queryFn: () => api<Audit[]>("/audit?limit=6&meaningful=true"),
    refetchInterval: interval,
    enabled: can("audit.read"),
  });
  const data = overview.data;

  return (
    <>
      <PageHeader
        title="Overview"
        description="System health, data movement, and issues requiring attention."
        eyebrow="WORKSPACE / OPERATIONS"
      >
        {can("pipelines.write") && (
          <Button asChild>
            <Link href="/pipelines/new">
              <Plus size={14} />
              Create pipeline
            </Link>
          </Button>
        )}
      </PageHeader>

      {overview.isPending ? (
        <Loading />
      ) : overview.isError ? (
        <ErrorPanel error={overview.error} retry={() => overview.refetch()} />
      ) : data ? (
        <>
          <OperationalSummary overview={data} isAdmin={isAdmin} />

          <div className="overview-grid">
            <Panel
              title="Data flow status"
              description="Capture runtime and movement health"
              actions={
                <Link className="text-link" href="/pipelines">
                  View pipelines
                </Link>
              }
            >
              <HealthDistribution
                overview={data}
                canCreate={can("pipelines.write")}
              />
            </Panel>
            {isAdmin && (
              <Panel
                title="System health"
                description="Control-plane infrastructure and connectivity"
                actions={
                  <Link className="text-link" href="/monitoring">
                    Open monitoring
                  </Link>
                }
              >
                <div className="infrastructure-list">
                  <InfrastructureRow
                    label="Kafka clusters"
                    healthy={data.healthy_kafka_clusters}
                    total={data.kafka_clusters}
                    href="/kafka/clusters"
                  />
                  <InfrastructureRow
                    label="Connect clusters"
                    healthy={data.healthy_connect_clusters}
                    total={data.connect_clusters}
                    href="/connect/clusters"
                  />
                  <InfrastructureRow
                    label="Source connections"
                    healthy={data.healthy_sources}
                    total={data.sources}
                    href="/sources"
                  />
                  <InfrastructureRow
                    label="Destinations"
                    healthy={data.destination_running}
                    total={data.destinations}
                    href="/destinations"
                  />
                </div>
              </Panel>
            )}

            {isAdmin && (
              <Panel
                title="Requires attention"
                description="Active operational incidents"
                actions={
                  <Link className="text-link" href="/operations/errors">
                    Error center
                  </Link>
                }
              >
                {errors.isPending ? (
                  <Loading />
                ) : errors.isError ? (
                  <ErrorPanel
                    error={errors.error}
                    retry={() => errors.refetch()}
                  />
                ) : errors.data.length ? (
                  <div className="attention-list">
                    {prioritizeIssues(errors.data).map((error) => (
                      <Link
                        key={error.id}
                        href={
                          error.destination_id
                            ? `/destinations/${error.destination_id}`
                            : error.pipeline_id
                              ? `/pipelines/${error.pipeline_id}`
                              : "/operations/errors"
                        }
                      >
                        <Status value={error.status} />
                        <div>
                          <strong>{error.message}</strong>
                          <small>
                            {error.category.replaceAll("_", " ")} ·{" "}
                            {relativeTime(error.created_at)}
                          </small>
                        </div>
                      </Link>
                    ))}
                  </div>
                ) : (
                  <div className="healthy-inline-state">
                    <ShieldCheck size={16} aria-hidden="true" />
                    <strong>No active incidents</strong>
                    <span>Everything looks operational.</span>
                  </div>
                )}
              </Panel>
            )}

            {isAdmin && (
              <Panel
                title="Recent activity"
                description="Latest configuration and lifecycle changes"
                actions={
                  <Link className="text-link" href="/operations/audit">
                    Audit trail
                  </Link>
                }
              >
                {activity.isPending ? (
                  <Loading />
                ) : activity.isError ? (
                  <ErrorPanel
                    error={activity.error}
                    retry={() => activity.refetch()}
                  />
                ) : activity.data.length ? (
                  <div className="overview-activity">
                    {collapseActivity(activity.data).map(({ entry, count }) => (
                      <div key={entry.id}>
                        <span className="activity-point" aria-hidden="true" />
                        <div>
                          <strong>{actionLabel(entry.action)}</strong>
                          <small>
                            {entry.actor} · {entry.resource_type}
                            {count > 1 && <b> ×{count}</b>} ·{" "}
                            {relativeTime(entry.created_at)}
                          </small>
                        </div>
                        <time
                          dateTime={entry.created_at}
                          title={date(entry.created_at)}
                        >
                          {new Intl.DateTimeFormat(undefined, {
                            hour: "2-digit",
                            minute: "2-digit",
                            hour12: false,
                          }).format(new Date(entry.created_at))}
                        </time>
                      </div>
                    ))}
                  </div>
                ) : (
                  <QuietState
                    icon={Activity}
                    title="No recent activity"
                    description="Configuration and lifecycle actions will appear here."
                  />
                )}
              </Panel>
            )}
          </div>

          <div className="overview-metrics-note">
            <ChartNoAxesCombined size={15} />
            <span>
              Live throughput:{" "}
              {data.throughput === null
                ? "unavailable"
                : `${number(data.throughput)}/s`}
              {" · "}CDC lag:{" "}
              {data.cdc_lag === null
                ? "unavailable"
                : `${number(data.cdc_lag)}ms`}
            </span>
            <Tooltip
              text={
                data.metrics_notice ||
                "Configure a metrics provider for historical throughput, lag, and freshness."
              }
            />
            {isAdmin && (
              <Link href="/monitoring" aria-label="Open monitoring">
                <ArrowRight size={14} />
              </Link>
            )}
          </div>
        </>
      ) : null}
    </>
  );
}
