"use client";
import Link from "next/link";
import { useId, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Database, Info } from "lucide-react";
import { Badge, Dialog } from "@cluecdc/ui";

type StatusTone = "good" | "bad" | "warn" | "info" | "snapshot" | "neutral";

const statusGroups: Record<StatusTone, string[]> = {
  good: [
    "RUNNING",
    "HEALTHY",
    "READY",
    "PASSED",
    "COMPLETED",
    "RESOLVED",
    "CONNECTED",
    "ENABLED",
    "STABLE",
    "SENT",
    "NON_BREAKING",
    "ACTIVE",
  ],
  bad: [
    "FAILED",
    "ERROR",
    "CRITICAL",
    "UNHEALTHY",
    "OPEN",
    "BREAKING",
    "UNAVAILABLE",
    "DISCONNECTED",
  ],
  warn: [
    "DEGRADED",
    "WARNING",
    "PENDING",
    "ACKNOWLEDGED",
    "POTENTIALLY_BREAKING",
    "RETRYING",
    "LAGGING",
  ],
  info: ["DEPLOYING", "RUNNING_JOB", "CREATE", "UPDATE"],
  snapshot: ["SNAPSHOTTING", "READ"],
  neutral: [],
};

function statusPresentation(value: string | null | undefined) {
  const status = (value || "UNKNOWN").toUpperCase();
  const tone =
    (Object.entries(statusGroups).find(([, values]) =>
      values.includes(status),
    )?.[0] as StatusTone | undefined) || "neutral";
  return { label: status.replaceAll("_", " ").toLowerCase(), tone };
}

export function StatusDot({ value }: { value: string | null | undefined }) {
  const { label, tone } = statusPresentation(value);
  return (
    <span
      className={`status-dot status-dot-${tone}`}
      role="img"
      aria-label={label}
    />
  );
}

export function StatusText({ value }: { value: string | null | undefined }) {
  const { label, tone } = statusPresentation(value);
  return (
    <span className={`status-text status-text-${tone}`}>
      <StatusDot value={value} />
      {label}
    </span>
  );
}

export function HealthIndicator({
  healthy,
  total,
}: {
  healthy: number;
  total: number;
}) {
  const state =
    total === 0 ? "NOT CONFIGURED" : healthy === total ? "HEALTHY" : "DEGRADED";
  return (
    <span className="health-indicator">
      <StatusText value={state} />
      <span className="technical-value">
        {healthy}/{total}
      </span>
    </span>
  );
}

export function StatusBadge({ value }: { value: string | null | undefined }) {
  const { label, tone } = statusPresentation(value);
  return (
    <Badge className={`signal-status status-${tone}`}>
      <span className="status-dot" aria-hidden="true" />
      {label}
    </Badge>
  );
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel operational-panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="panel-actions">{actions}</div>}
      </div>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  href,
  unavailable = false,
}: {
  label: string;
  value: ReactNode;
  detail: ReactNode;
  icon: LucideIcon;
  href?: string;
  unavailable?: boolean;
}) {
  const contents = (
    <>
      <div className="metric-title">
        <span>{label}</span>
        <Icon size={16} aria-hidden="true" />
      </div>
      <strong className={unavailable ? "unavailable" : ""}>{value}</strong>
      <small>{detail}</small>
    </>
  );
  return href ? (
    <Link className="metric-card" href={href}>
      {contents}
    </Link>
  ) : (
    <div className="metric-card">{contents}</div>
  );
}

export function QuietState({
  title,
  description,
  icon: Icon = Database,
}: {
  title: string;
  description: string;
  icon?: LucideIcon;
}) {
  return (
    <div className="quiet-state">
      <Icon size={20} aria-hidden="true" />
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton-group" role="status" aria-label="Loading data">
      <span className="sr-only">Loading data</span>
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton-row" key={i}>
          <span />
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}

export function Tooltip({
  text,
  children,
}: {
  text: string;
  children?: ReactNode;
}) {
  const id = useId();
  return (
    <span className="tooltip-trigger" tabIndex={0} aria-describedby={id}>
      {children || <Info size={13} aria-label="More information" />}
      <span id={id} className="tooltip-content" role="tooltip">
        {text}
      </span>
    </span>
  );
}

export function DetailDrawer(props: React.ComponentProps<typeof Dialog>) {
  return <Dialog {...props} variant="drawer" />;
}

export function FilterBar({ children }: { children: ReactNode }) {
  return (
    <div className="filter-bar" role="group" aria-label="Resource filters">
      {children}
    </div>
  );
}

export function InventoryStrip({
  items,
}: {
  items: { label: string; value: ReactNode }[];
}) {
  return (
    <dl className="inventory-strip" aria-label="Resource inventory">
      {items.map((item) => (
        <div key={item.label}>
          <dd>{item.value}</dd>
          <dt>{item.label}</dt>
        </div>
      ))}
    </dl>
  );
}

export function TimeRangeSelector() {
  return (
    <select
      aria-label="Metrics time range"
      disabled
      title="Historical metrics require a configured metrics provider"
      defaultValue="24h"
    >
      <option value="1h">Last 1h</option>
      <option value="6h">Last 6h</option>
      <option value="24h">Last 24h</option>
      <option value="7d">Last 7d</option>
    </select>
  );
}
