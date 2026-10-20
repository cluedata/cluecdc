"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Boxes,
  ChartNoAxesCombined,
  CircleDashed,
  Database,
  GitBranch,
  Plus,
  Radio,
  Send,
  ShieldCheck,
  Timer,
  Waypoints,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type {
  Audit,
  Json,
  OperationalError,
  Overview,
  Pipeline,
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

function WorkspaceFlow({
  overview,
  onboarding = false,
}: {
  overview?: Overview;
  onboarding?: boolean;
}) {
  const nodes: {
    label: string;
    detail: string;
    icon: LucideIcon;
    tone: string;
    state?: string;
  }[] = onboarding
    ? [
        {
          label: "Source",
          detail: "PostgreSQL or MySQL",
          icon: Database,
          tone: "violet",
        },
        {
          label: "Capture",
          detail: "Debezium connector",
          icon: GitBranch,
          tone: "blue",
        },
        {
          label: "Kafka",
          detail: "Durable topics",
          icon: Radio,
          tone: "cyan",
        },
        {
          label: "Delivery",
          detail: "Kafka Connect sinks",
          icon: Send,
          tone: "teal",
        },
        {
          label: "Destination",
          detail: "Database or object storage",
          icon: Boxes,
          tone: "orange",
        },
      ]
    : [
        {
          label: "Sources",
          detail: `${overview?.healthy_sources ?? 0} of ${overview?.sources ?? 0} healthy`,
          icon: Database,
          tone: "violet",
          state:
            overview?.sources && overview.healthy_sources === overview.sources
              ? "HEALTHY"
              : "DEGRADED",
        },
        {
          label: "Capture",
          detail: `${overview?.running ?? 0} pipelines running`,
          icon: GitBranch,
          tone: "blue",
          state:
            overview?.failed || overview?.degraded ? "DEGRADED" : "HEALTHY",
        },
        {
          label: "Kafka",
          detail: `${overview?.healthy_kafka_clusters ?? 0} of ${overview?.kafka_clusters ?? 0} healthy`,
          icon: Radio,
          tone: "cyan",
          state:
            overview?.kafka_clusters &&
            overview.healthy_kafka_clusters === overview.kafka_clusters
              ? "HEALTHY"
              : "DEGRADED",
        },
        {
          label: "Delivery",
          detail: `${overview?.delivery_running ?? 0} of ${overview?.deliveries ?? 0} running`,
          icon: Send,
          tone: "teal",
          state:
            overview?.delivery_failed || overview?.delivery_degraded
              ? "DEGRADED"
              : "HEALTHY",
        },
        {
          label: "Destinations",
          detail: `${overview?.destination_running ?? 0} of ${overview?.destinations ?? 0} active`,
          icon: Boxes,
          tone: "orange",
          state:
            overview?.destination_failed || overview?.destination_degraded
              ? "DEGRADED"
              : "HEALTHY",
        },
      ];

  return (
    <ol
      className={`workspace-flow-map ${onboarding ? "workspace-flow-onboarding" : ""}`}
      aria-label="Change data capture flow"
    >
      {nodes.map((node, index) => (
        <li key={node.label}>
          <div className={`workspace-flow-node flow-${node.tone}`}>
            <span className="flow-node-icon">
              <node.icon size={19} aria-hidden="true" />
            </span>
            <span>
              <strong>{node.label}</strong>
              <small>{node.detail}</small>
            </span>
            {node.state && <StatusText value={node.state} />}
          </div>
          {index < nodes.length - 1 && (
            <span className="workspace-flow-connector" aria-hidden="true">
              <i />
              <ArrowRight size={15} />
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

function readinessState(healthy: number, total: number) {
  return total === 0
    ? "NOT_CONFIGURED"
    : healthy === total
      ? "HEALTHY"
      : "DEGRADED";
}

function ReadinessItem({
  label,
  description,
  healthy,
  total,
  href,
  icon: Icon,
}: {
  label: string;
  description: string;
  healthy: number;
  total: number;
  href: string;
  icon: LucideIcon;
}) {
  return (
    <Link className="readiness-item" href={href}>
      <span className="readiness-icon">
        <Icon size={18} aria-hidden="true" />
      </span>
      <span className="readiness-copy">
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <span className="readiness-status">
        <StatusText value={readinessState(healthy, total)} />
        <small>{total ? `${healthy}/${total}` : "Configure"}</small>
      </span>
      <ArrowRight size={15} aria-hidden="true" />
    </Link>
  );
}

function EmptyWorkspace({
  overview,
  canCreate,
  isAdmin,
}: {
  overview: Overview;
  canCreate: boolean;
  isAdmin: boolean;
}) {
  return (
    <section
      className="workspace-onboarding"
      aria-labelledby="workspace-start-title"
    >
      <div className="onboarding-hero">
        <div className="onboarding-copy">
          <span className="onboarding-kicker">
            WELCOME TO YOUR CDC WORKSPACE
          </span>
          <h2 id="workspace-start-title">
            Build your first reliable data movement path
          </h2>
          <p>
            ClueCDC is the control plane for capturing database changes,
            streaming them through Kafka, and delivering them to the systems
            that need them.
          </p>
          {canCreate ? (
            <Button asChild>
              <Link href="/pipelines/new">
                <Plus size={15} />
                Create your first pipeline
              </Link>
            </Button>
          ) : (
            <p className="onboarding-permission-note">
              You can explore workspace readiness. Ask an administrator to
              create the first pipeline.
            </p>
          )}
        </div>
        <div className="onboarding-visual">
          <div className="onboarding-visual-label">
            <Waypoints size={16} aria-hidden="true" />
            How data moves through ClueCDC
          </div>
          <WorkspaceFlow onboarding />
        </div>
      </div>

      {isAdmin && (
        <div className="onboarding-readiness">
          <div className="readiness-heading">
            <div>
              <span>DEPLOYMENT PREREQUISITES</span>
              <h3>Workspace readiness</h3>
            </div>
            <p>Configure these dependencies before deployment.</p>
          </div>
          <div className="readiness-grid">
            <ReadinessItem
              label="Kafka clusters"
              description="Event transport"
              healthy={overview.healthy_kafka_clusters}
              total={overview.kafka_clusters}
              href="/kafka/clusters"
              icon={Radio}
            />
            <ReadinessItem
              label="Connect clusters"
              description="Connector runtime"
              healthy={overview.healthy_connect_clusters}
              total={overview.connect_clusters}
              href="/connect/clusters"
              icon={Boxes}
            />
            <ReadinessItem
              label="Source connections"
              description="Capture origins"
              healthy={overview.healthy_sources}
              total={overview.sources}
              href="/sources"
              icon={Database}
            />
            <ReadinessItem
              label="Destinations"
              description="Delivery targets"
              healthy={overview.destination_running}
              total={overview.destinations}
              href="/destinations"
              icon={Send}
            />
          </div>
        </div>
      )}
    </section>
  );
}

function KpiCard({
  label,
  value,
  detail,
  href,
  tone,
  icon: Icon,
}: {
  label: string;
  value: number;
  detail: string;
  href: string;
  tone: "violet" | "blue" | "teal" | "orange";
  icon: LucideIcon;
}) {
  return (
    <Link className={`spectrum-kpi spectrum-kpi-${tone}`} href={href}>
      <span className="spectrum-kpi-icon">
        <Icon size={18} aria-hidden="true" />
      </span>
      <span className="spectrum-kpi-label">{label}</span>
      <strong>{number(value)}</strong>
      <small>{detail}</small>
      <ArrowRight size={15} aria-hidden="true" />
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
  const attention = overview.degraded + overview.failed;
  return (
    <section className="spectrum-kpi-grid" aria-label="Operational summary">
      <KpiCard
        label="Total pipelines"
        value={overview.pipelines}
        detail={`${overview.running} currently running`}
        href="/pipelines"
        tone="violet"
        icon={GitBranch}
      />
      <KpiCard
        label="Running"
        value={overview.running}
        detail={`${overview.paused} paused`}
        href="/pipelines?status=RUNNING"
        tone="blue"
        icon={Activity}
      />
      <KpiCard
        label="Needs attention"
        value={attention}
        detail={`${overview.failed} failed · ${overview.degraded} degraded`}
        href="/pipelines?status=DEGRADED"
        tone="teal"
        icon={AlertTriangle}
      />
      <KpiCard
        label={isAdmin ? "Active incidents" : "Paused"}
        value={isAdmin ? overview.errors : overview.paused}
        detail={
          isAdmin
            ? overview.errors
              ? "Open error center"
              : "No active incidents"
            : `${overview.unknown} unknown state`
        }
        href={isAdmin ? "/operations/errors" : "/pipelines?status=PAUSED"}
        tone="orange"
        icon={isAdmin ? ShieldCheck : CircleDashed}
      />
    </section>
  );
}

function PipelineOverview({ pipelines }: { pipelines: Pipeline[] }) {
  if (!pipelines.length) {
    return (
      <QuietState
        icon={GitBranch}
        title="Pipeline inventory is syncing"
        description="Configured pipelines will appear here when inventory is available."
      />
    );
  }
  return (
    <div className="pipeline-overview-table-wrap">
      <table className="pipeline-overview-table">
        <thead>
          <tr>
            <th>Pipeline</th>
            <th>Source</th>
            <th>Tables</th>
            <th>Deliveries</th>
            <th>Status</th>
            <th>Last activity</th>
          </tr>
        </thead>
        <tbody>
          {pipelines.slice(0, 5).map((pipeline) => (
            <tr key={pipeline.id}>
              <td>
                <Link href={`/pipelines/${pipeline.id}`}>{pipeline.name}</Link>
              </td>
              <td>{pipeline.source_name || "Configured source"}</td>
              <td className="technical-value">{pipeline.tables}</td>
              <td className="technical-value">
                {pipeline.destinations ??
                  pipeline.deliveries_summary?.length ??
                  0}
              </td>
              <td>
                <Status
                  value={pipeline.aggregate_status || pipeline.actual_state}
                />
              </td>
              <td>{relativeTime(pipeline.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
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
  const populated = (overview.data?.pipelines || 0) > 0;
  const errors = useQuery({
    queryKey: ["errors", "recent-active"],
    queryFn: () =>
      api<OperationalError[]>("/operations/errors?limit=5&active=true"),
    refetchInterval: interval,
    enabled: isAdmin && populated,
  });
  const activity = useQuery({
    queryKey: ["audit", "meaningful"],
    queryFn: () => api<Audit[]>("/audit?limit=6&meaningful=true"),
    refetchInterval: interval,
    enabled: can("audit.read") && populated,
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api<Pipeline[]>("/pipelines"),
    refetchInterval: interval,
    enabled: populated,
  });
  const data = overview.data;

  return (
    <>
      <PageHeader
        title="Overview"
        description="See how change data moves, what is healthy, and where your attention is needed."
        eyebrow="WORKSPACE / DATA MOVEMENT"
      >
        {can("pipelines.write") && data?.pipelines !== 0 && (
          <Button asChild>
            <Link href="/pipelines/new">
              <Plus size={15} />
              Create pipeline
            </Link>
          </Button>
        )}
      </PageHeader>

      {overview.isPending ? (
        <Loading />
      ) : overview.isError ? (
        <ErrorPanel error={overview.error} retry={() => overview.refetch()} />
      ) : data?.pipelines === 0 ? (
        <EmptyWorkspace
          overview={data}
          canCreate={can("pipelines.write")}
          isAdmin={isAdmin}
        />
      ) : data ? (
        <>
          <OperationalSummary overview={data} isAdmin={isAdmin} />

          <div className="spectrum-overview-primary">
            <Panel
              className="workspace-flow-panel"
              title="Data movement"
              description="Live health across the capture and delivery path"
              actions={
                <Link className="text-link" href="/pipelines">
                  View pipelines
                </Link>
              }
            >
              <WorkspaceFlow overview={data} />
            </Panel>

            {isAdmin && (
              <Panel
                className="attention-panel"
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
                ) : errors.data?.length ? (
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
          </div>

          <div className="spectrum-overview-secondary">
            <Panel
              className="pipeline-overview-panel"
              title="Pipeline overview"
              description="Recent operational state across configured pipelines"
              actions={
                <Link className="text-link" href="/pipelines">
                  View all
                </Link>
              }
            >
              {pipelines.isPending ? (
                <Loading />
              ) : pipelines.isError ? (
                <ErrorPanel
                  error={pipelines.error}
                  retry={() => pipelines.refetch()}
                />
              ) : (
                <PipelineOverview pipelines={pipelines.data || []} />
              )}
            </Panel>

            {isAdmin && (
              <Panel
                className="system-health-panel"
                title="System health"
                description="Control-plane infrastructure and connectivity"
                actions={
                  <Link className="text-link" href="/monitoring">
                    Monitoring
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

            {isAdmin && !!activity.data?.length && (
              <Panel
                className="activity-panel"
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
                ) : activity.data?.length ? (
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

          <div className="overview-metrics-note spectrum-metrics-note">
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
