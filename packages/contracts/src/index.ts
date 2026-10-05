export type Json =
  string | number | boolean | null | Json[] | { [key: string]: Json };
export interface Entity {
  id: string;
  created_at: string;
  updated_at: string;
}
export interface Source extends Entity {
  name: string;
  type: "postgresql" | "mysql";
  environment: string;
  host: string;
  port: number;
  database_name: string;
  username: string;
  ssl_enabled: boolean;
  provider_options: Record<string, string | number | boolean>;
  status: string;
  last_health_check_at: string | null;
  last_discovery_at?: string | null;
  tables?: number;
  cdc_ready_tables?: number;
}
export interface SourceTable extends Entity {
  source_id: string;
  schema_name: string;
  table_name: string;
  primary_key_columns: string[];
  estimated_rows: number | null;
  estimated_size_bytes: number | null;
  cdc_ready: boolean;
  cdc_status: string;
  cdc_issues: string[];
  columns_json: {
    name: string;
    type: string;
    nullable: boolean;
    ordinal: number;
  }[];
  indexes_json: { name: string; definition: string }[];
}
export interface KafkaCluster extends Entity {
  name: string;
  bootstrap_servers: string;
  security_protocol: string;
  status: string;
}
export interface ConnectCluster extends Entity {
  name: string;
  base_url: string;
  kafka_cluster_id: string;
  status: string;
}
export interface Connector extends Entity {
  name: string;
  actual_state: string;
  desired_state: string;
  connector_class: string;
  config_json: Record<string, string>;
  runtime_json: Json;
  connect_cluster_id: string;
  connector_type: "source" | "sink";
  related_resource?: { name: string; href: string } | null;
}
export interface Pipeline extends Entity {
  name: string;
  source_id: string;
  kafka_cluster_id: string;
  connect_cluster_id: string;
  connector_id: string | null;
  topic_prefix: string;
  snapshot_mode: string;
  desired_state: string;
  actual_state: string;
  tables: number;
  throughput: number | null;
  cdc_lag: number | null;
  source_name?: string;
  destinations?: number;
  delivery_state?: string;
  aggregate_status?: PipelineStatus;
  status_reason?: string;
  kafka_name?: string;
  deliveries_summary?: {
    id: string;
    name: string;
    destination_id: string;
    destination_name: string;
    actual_state: string;
  }[];
  topics?: string[];
}
export type PipelineStatus =
  | "HEALTHY"
  | "DEGRADED"
  | "FAILED"
  | "PAUSED"
  | "CREATING"
  | "UPDATING"
  | "UNKNOWN";
export interface PipelineTable extends Entity {
  pipeline_id: string;
  schema_name: string;
  table_name: string;
  topic_name: string;
  snapshot_status: string;
  cdc_status: string;
  primary_key_columns: string[];
  destination_schema: string | null;
  destination_table: string | null;
  initial_data_strategy: string;
  delete_strategy: string;
  schema_status: string;
  destination_status: string;
  snapshot_rows_processed: number | null;
  snapshot_estimated_rows: number | null;
  snapshot_current_chunk: string | null;
  snapshot_started_at: string | null;
  snapshot_last_activity_at: string | null;
  snapshot_finished_at: string | null;
  removed_at: string | null;
}
export interface PipelineOperation extends Entity {
  pipeline_id: string;
  table_id: string | null;
  type: string;
  status:
    "PENDING" | "RUNNING" | "WAITING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  current_step: string | null;
  progress: number | null;
  error_code: string | null;
  error_message: string | null;
  metadata_json: Json;
  started_at: string | null;
  finished_at: string | null;
}
export interface PipelineDetail extends Omit<
  Pipeline,
  "tables" | "destinations"
> {
  destinations: Delivery[];
  tables: PipelineTable[];
  source: Source;
  kafka_cluster: KafkaCluster;
  connect_cluster: ConnectCluster;
  connector: Connector | null;
  config: Record<string, string>;
  metrics: {
    throughput: number | null;
    cdc_lag: number | null;
    snapshot_state: string;
    notice: string;
  };
}
export interface Runtime {
  actual_state: string;
  desired_state: string;
  connector?: { state: string; worker_id: string };
  tasks: { id: number; state: string; worker_id: string; error?: string }[];
  error?: { code: string; message: string };
}
export interface Job extends Entity {
  kind: string;
  status: string;
  error: string | null;
  result_json: Json;
}
export interface Readiness {
  status: string;
  checks: {
    key: string;
    name: string;
    status: string;
    severity: string;
    description: string;
    current_value: Json;
    expected_value: Json;
    recommended_action: string | null;
  }[];
}
export interface Topic {
  name: string;
  partitions: number;
  replication_factor: number | null;
  message_rate: number | null;
  retention: string | null;
  size: number | null;
  partition_details: Json[];
  internal: boolean;
  protected: boolean;
  protection_reason: string | null;
  used_by_pipelines?: {
    id: string;
    name: string;
    desired_state: string;
    actual_state: string;
    active: boolean;
  }[];
}
export interface ConsumerGroupOffset {
  topic: string;
  partition: number;
  committed_offset: number;
  end_offset: number | null;
  lag: number | null;
}
export interface ConsumerGroup {
  group_id: string;
  cluster_id: string;
  cluster_name: string;
  state: string;
  protocol_type: string;
  protocol: string;
  members: number;
  topics: string[];
  offsets: ConsumerGroupOffset[];
  total_lag: number;
  connector_name?: string;
  resource_type?: "delivery";
  resource_id?: string;
  resource_name?: string;
}
export interface ConsumerGroupClusterReport {
  id: string;
  name: string;
  status: string;
  group_count: number;
  error: { code: string; message: string } | null;
}
export interface ConsumerGroupReport {
  observed_at: string;
  clusters: ConsumerGroupClusterReport[];
  groups: ConsumerGroup[];
}
export interface Audit extends Entity {
  actor: string;
  action: string;
  resource_type: string;
  resource_id: string;
  before_json: Json;
  after_json: Json;
}
export interface OperationalError extends Entity {
  pipeline_id: string | null;
  connector_name: string | null;
  task_id: number | null;
  category: string;
  severity: string;
  message: string;
  status: string;
  destination_id?: string | null;
  connector_id?: string | null;
}
export type AlertSeverity = "info" | "warning" | "critical";
export type AlertStatus = "firing" | "acknowledged" | "silenced" | "resolved";
export interface Alert extends Entity {
  fingerprint: string;
  event_type: string;
  severity: AlertSeverity;
  status: AlertStatus;
  source_type: string;
  source_id: string | null;
  source_name: string | null;
  pipeline_id: string | null;
  pipeline_name: string | null;
  component: string;
  title: string;
  message: string;
  details: Record<string, Json>;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  silenced_until: string | null;
  silenced_by: string | null;
  occurrence_count: number;
}
export interface NotificationDelivery extends Entity {
  alert_id: string;
  channel_id: string;
  channel_name: string;
  channel_type: "slack" | "telegram" | "webhook";
  kind: "firing" | "resolved";
  status: "pending" | "sending" | "sent" | "failed";
  attempt_count: number;
  last_error: string | null;
  next_attempt_at: string;
  sent_at: string | null;
}
export interface AlertDetail extends Alert {
  deliveries: NotificationDelivery[];
}
export interface AlertPage {
  items: Alert[];
  page: number;
  pageSize: number;
  total: number;
}
export interface AlertSummary {
  active_count: number;
  recent: Alert[];
}
export interface NotificationChannel extends Entity {
  name: string;
  type: "slack" | "telegram" | "webhook";
  enabled: boolean;
  config: { configured: boolean; maskedValue: string };
}
export interface AlertRule extends Entity {
  name: string;
  description: string;
  enabled: boolean;
  severity: AlertSeverity | null;
  event_types: string[];
  source_filters: string[];
  pipeline_filters: string[];
  connector_filters: string[];
  channel_ids: string[];
  cooldown_seconds: number;
  notification_policy:
    | "notify_every_occurrence"
    | "notify_after_cooldown"
    | "notify_first_occurrence_only";
  send_recovery: boolean;
}
export interface SchemaVersion extends Entity {
  source_id: string;
  schema_name: string;
  table_name: string;
  version: number;
  schema_hash: string;
  schema_json: Json;
  diff_json: Json[];
}
export interface Overview {
  sources: number;
  deliveries: number;
  delivery_running: number;
  delivery_failed: number;
  delivery_degraded: number;
  delivery_paused: number;
  delivery_unknown: number;
  healthy_sources: number;
  pipelines: number;
  running: number;
  degraded: number;
  failed: number;
  unknown: number;
  paused: number;
  kafka_clusters: number;
  connect_clusters: number;
  errors: number;
  throughput: number | null;
  cdc_lag: number | null;
  metrics_notice: string;
  destinations: number;
  destination_running: number;
  destination_failed: number;
  destination_degraded: number;
  destination_paused: number;
  healthy_kafka_clusters: number;
  healthy_connect_clusters: number;
}

export interface TopicMapping {
  topic: string;
  schema_name: string;
  table_name: string;
}
export interface DeliveryConfiguration {
  delivery_type?: "DATABASE" | "OBJECT_STORAGE";
  compression?: "gzip" | "none";
  file_max_records?: number;
  flush_interval_ms?: number;
  file_name_template?: string;
  pipeline_id: string;
  connect_cluster_id?: string;
  name: string;
  write_mode: "upsert" | "insert";
  primary_key_mode: "record_key";
  auto_create: boolean;
  auto_evolve: boolean;
  delete_enabled: boolean;
  null_handling: "ignore";
  batch_size: number;
  max_retries: number;
  retry_backoff_ms: number;
  tasks_max: number;
}
export interface Delivery extends Entity {
  delivery_type?: "DATABASE" | "OBJECT_STORAGE";
  destination_id: string;
  pipeline_id: string;
  pipeline_name: string;
  connector_id: string | null;
  name: string;
  delivery_mode: string;
  topic_mapping_json: TopicMapping[];
  configuration_json: DeliveryConfiguration;
  desired_state: string;
  actual_state: string;
  destination: Destination;
  connector: Connector | null;
  connect_cluster?: ConnectCluster | null;
  pipeline?: Pipeline;
  topics?: string[];
  tasks?: { id: number; state: string; worker_id: string; error?: string }[];
  metrics?: {
    lag: number | null;
    throughput: number | null;
    last_message: string | null;
    notice: string;
  };
}
export interface Destination extends Entity {
  name: string;
  description: string;
  type: "postgresql" | "mysql";
  environment: "DEV" | "STAGING" | "PROD";
  host: string;
  port: number;
  database_name: string;
  username: string;
  ssl_enabled: boolean;
  provider_options: Record<string, string | number | boolean>;
  status: string;
  last_health_check_at: string | null;
  connected_pipelines: number;
  delivery_count: number;
  desired_state: string;
  actual_state: string;
  last_delivery: string | null;
  records_per_second: number | null;
  delivery_lag: number | null;
  metrics_notice: string;
  deliveries: Delivery[];
}
export interface DatabaseProviderMetadata {
  type: "postgresql" | "mysql";
  display_name: string;
  icon: string;
  default_port: number;
  namespace_label: string;
  source_supported: boolean;
  destination_supported: boolean;
  capabilities: string[];
  setup_instructions: string[];
}
export interface DeliveryRuntime extends Runtime {
  delivery_id: string;
  pipeline_id: string;
  name: string;
  connector_id: string;
}
export interface DestinationRuntime {
  actual_state: string;
  desired_state: string;
  deliveries: DeliveryRuntime[];
}
export interface ConnectorPlugin {
  class: string;
  type: string;
  version: string;
}

export type ConnectionCategory = "DATABASE" | "OBJECT_STORAGE";
export type ConnectionProvider = "POSTGRESQL" | "MYSQL" | "AWS_S3" | "MINIO";
export interface Connection extends Entity {
  name: string;
  type: ConnectionProvider;
  category: ConnectionCategory;
  provider: ConnectionProvider;
  status: string;
  description: string;
  config: Record<string, Json>;
  credentials: { configured: boolean; masked_value: string };
  last_tested_at: string | null;
  last_test_status: string | null;
  last_test_message: string | null;
  last_checked: string | null;
  capabilities: ("SOURCE" | "DESTINATION")[];
  used_by?: { type: string; id: string; name: string; pipeline?: string }[];
  used_by_count?: number;
}
export interface ConnectionProviderMetadata {
  provider: ConnectionProvider;
  category: ConnectionCategory;
  name: string;
  description: string;
}
export interface ConnectionCheck {
  name: string;
  status: "success" | "failed" | "skipped";
  message?: string;
}
export interface ConnectionTestResult {
  success: boolean;
  status?: string;
  checks: ConnectionCheck[];
}
