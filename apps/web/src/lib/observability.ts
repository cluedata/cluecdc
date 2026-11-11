export type MetricEvidence = {
  value: number | null;
  unit: string;
  status:
    | "AVAILABLE"
    | "UNAVAILABLE"
    | "UNSUPPORTED"
    | "INCONCLUSIVE"
    | "PARTIAL"
    | "STALE";
  confidence: "DIRECT" | "INDIRECT" | "UNKNOWN";
  observed_at: string | null;
  collected_at: string;
  source: string;
  reason_code: string;
};
export type Observation = {
  schema_version: 1;
  pipeline_id: string;
  name: string;
  source_provider: string;
  operational_state: string;
  connector_state: string;
  collected_at: string | null;
  last_success_at: string | null;
  status: MetricEvidence["status"];
  capture_lag: MetricEvidence;
  capture_events: MetricEvidence;
  deliveries: {
    delivery_id: string;
    name: string;
    connector_state: string;
    operational_state: string;
    task_states: Record<string, string>;
    group_id: string | null;
    group_state: string | null;
    required_partitions: number;
    valid_partitions: number;
    required_tasks: number;
    verified_task_workers: number;
    backlog: MetricEvidence;
    sink_metrics: Record<string, MetricEvidence>;
    destination_progress: MetricEvidence;
  }[];
  snapshots: {
    table_id: string;
    table: string;
    kind: string;
    state: string;
    started_at: string | null;
    last_progress_at: string | null;
    completed_at: string | null;
    processed_rows: MetricEvidence;
    estimated_rows: number | null;
    percentage: number | null;
    current_chunk: string | null;
    provenance: string;
    stale: boolean;
  }[];
  findings: {
    code: string;
    severity: string;
    delivery_id: string | null;
    observations: string[];
    explanation: string;
    suggested_action: string;
    observed_at: string;
    confidence: string;
  }[];
};
export type ObservationHistory = {
  points: (Observation & { collected_at: string })[];
  gaps: { start: string; end: string; reason_code: string }[];
  truncated: boolean;
};
export function evidenceText(metric: MetricEvidence): string {
  if (metric.status === "AVAILABLE" && metric.value !== null)
    return `${metric.value.toLocaleString()} ${metric.unit}`;
  return {
    UNAVAILABLE: "Unavailable",
    UNSUPPORTED: "Unsupported",
    STALE: "Stale",
    INCONCLUSIVE: "Insufficient evidence",
    PARTIAL: "Insufficient evidence",
    AVAILABLE: "Unavailable",
  }[metric.status];
}
