export const ruleNames = [
  "row_count",
  "row_count_diff",
  "last_updated",
  "freshness_delay",
  "data_age",
  "schema_match",
] as const;
export type RuleType = (typeof ruleNames)[number];
export const explanations: Record<RuleType, string> = {
  row_count:
    "Compares actual counts. Equal counts do not establish record equality.",
  row_count_diff:
    "Compares count differences against an absolute or percentage threshold.",
  last_updated: "Compares latest timestamps with the same declared semantics.",
  freshness_delay:
    "Measures signed Source minus Destination delay. Ahead timestamps require investigation.",
  data_age:
    "Measures the age of Destination data. Source activity gates failures by default.",
  schema_match:
    "Compares current column definitions. Period settings do not apply.",
};
export const label = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
export type RuleConfig = {
  id?: string;
  type: RuleType;
  enabled?: boolean;
  [key: string]:
    string | number | boolean | string[] | Record<string, string> | undefined;
};
export type Dataset = {
  connection_id: string;
  database: string;
  schema_name: string;
  table: string;
  data_model: "CURRENT_STATE";
  current_state_confirmed: boolean;
};
export type Predicate = {
  column: string;
  operator: string;
  value?: string | number | boolean | null;
};
export type Configuration = {
  name: string;
  description: string;
  source: Dataset;
  destination: Dataset;
  enabled: boolean;
  pipeline_id?: string | null;
  rules: RuleConfig[];
  comparison: {
    scope: "FULL_TABLE" | "TIME_WINDOW";
    consistency: "BEST_EFFORT";
    period: "ROLLING" | "FIXED";
    lookback_seconds: number;
    settling_seconds: number;
    source_time_column?: string | null;
    destination_time_column?: string | null;
    source_timezone?: string | null;
    destination_timezone?: string | null;
    fixed_start?: string | null;
    fixed_end?: string | null;
    source_filters: Predicate[];
    destination_filters: Predicate[];
    window_semantics?: string;
  };
  schedule: {
    enabled: boolean;
    frequency_seconds: number;
    timeout_seconds: number;
    display_timezone: string;
    full_scan_acknowledged: boolean;
    retention_days: number;
  };
  alerting: {
    channel_id: string | null;
    enabled: boolean | null;
    severity: string;
    failure_threshold: number;
    cooldown_seconds: number;
    send_recovery: boolean;
    on_fail: boolean;
    on_error: boolean;
    on_inconclusive: boolean;
  };
};
export type Check = {
  related_pipelines?: { id: string; name: string }[];
  source_name?: string;
  destination_name?: string;
  id: string;
  name: string;
  enabled: boolean;
  configuration: Configuration;
  version: number;
  next_run_at: string | null;
  active_run_id: string | null;
  latest_run: Run | null;
  alert_status: {
    configured: boolean;
    available: boolean;
    channel_name: string | null;
    reason: string | null;
  };
  alerts?: {
    id: string;
    event_type: string;
    status: string;
    message: string;
  }[];
};
export type Run = {
  id: string;
  check_id: string;
  job_id: string;
  trigger: string;
  status: string;
  quality: string | null;
  started_at: string | null;
  finished_at: string | null;
  scheduled_at: string;
  consistency: string;
  version: number;
  error_code: string | null;
  window_json: Record<string, string>;
  results?: Result[];
  snapshot_json?: Configuration;
};
export type Result = {
  id: string;
  rule_type: string;
  quality: string;
  evidence_json: Record<string, unknown>;
};
export type Page<T> = {
  items: T[];
  has_more: boolean;
  offset: number;
  limit: number;
};
export type Connection = {
  id: string;
  name: string;
  database: string;
  provider: string;
  supported: boolean;
  reason: string | null;
  capabilities: string[];
};
export type Column = {
  column_name: string;
  data_type: string;
  canonical: { family: string; aware?: boolean };
};
export type Capability = {
  status: string;
  rules: { rule_type: string; status: string; reasons: string[] }[];
  warnings: string[];
};
export const emptyDataset = (): Dataset => ({
  connection_id: "",
  database: "",
  schema_name: "",
  table: "",
  data_model: "CURRENT_STATE",
  current_state_confirmed: false,
});
export const newConfiguration = (): Configuration => ({
  name: "",
  description: "",
  enabled: true,
  source: emptyDataset(),
  destination: emptyDataset(),
  rules: [],
  comparison: {
    scope: "FULL_TABLE",
    consistency: "BEST_EFFORT",
    period: "ROLLING",
    lookback_seconds: 3600,
    settling_seconds: 120,
    source_timezone: "UTC",
    destination_timezone: "UTC",
    source_filters: [],
    destination_filters: [],
  },
  schedule: {
    enabled: false,
    frequency_seconds: 600,
    timeout_seconds: 120,
    display_timezone: "UTC",
    full_scan_acknowledged: false,
    retention_days: 90,
  },
  alerting: {
    channel_id: null,
    enabled: null,
    severity: "warning",
    failure_threshold: 2,
    cooldown_seconds: 900,
    send_recovery: true,
    on_fail: true,
    on_error: true,
    on_inconclusive: false,
  },
});
export const defaultRule = (type: RuleType): RuleConfig => {
  if (["last_updated", "freshness_delay", "data_age"].includes(type))
    return {
      type,
      source_column: "",
      destination_column: "",
      semantics: "BUSINESS_UPDATED",
      source_timezone: "UTC",
      destination_timezone: "UTC",
      ...(type === "data_age"
        ? { activity_policy: "ONLY_WHEN_SOURCE_ACTIVE" }
        : {}),
    };
  return { type };
};
