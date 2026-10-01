import type {
  Connector,
  Delivery,
  PipelineDetail,
  PipelineStatus,
} from "@cluecdc/contracts";

const failed = new Set(["FAILED", "ERROR", "UNHEALTHY", "UNAVAILABLE"]);
const degraded = new Set(["DEGRADED", "WARNING"]);
const paused = new Set(["PAUSED", "STOPPED"]);
const transitional = new Set(["DEPLOYING", "CREATING", "PENDING"]);

export type PipelineHealth = {
  status: PipelineStatus;
  reason: string;
};

export function derivePipelineHealth(
  captureState: string | null | undefined,
  deliveries: Pick<Delivery, "actual_state" | "name">[],
  destinationStates: string[] = [],
  lag: number | null = null,
  lagThresholdMs = 5000,
): PipelineHealth {
  const capture = (captureState || "UNKNOWN").toUpperCase();
  const deliveryStates = deliveries.map((item) =>
    (item.actual_state || "UNKNOWN").toUpperCase(),
  );
  const targets = destinationStates.map((state) => state.toUpperCase());
  const all = [capture, ...deliveryStates, ...targets];

  if (failed.has(capture))
    return { status: "FAILED", reason: "Capture connector failed" };
  const failedDelivery = deliveries.find((item) =>
    failed.has((item.actual_state || "UNKNOWN").toUpperCase()),
  );
  if (failedDelivery)
    return {
      status: "FAILED",
      reason: `Delivery ${failedDelivery.name} failed`,
    };
  if (targets.some((state) => failed.has(state)))
    return { status: "FAILED", reason: "Destination is unavailable" };
  if (all.some((state) => paused.has(state)))
    return { status: "PAUSED", reason: "A connector is paused" };
  if (all.some((state) => degraded.has(state)))
    return { status: "DEGRADED", reason: "A runtime component is degraded" };
  if (lag !== null && lag > lagThresholdMs)
    return {
      status: "DEGRADED",
      reason: `Consumer lag exceeds ${lagThresholdMs.toLocaleString()} ms`,
    };
  if (all.some((state) => transitional.has(state)))
    return { status: "CREATING", reason: "Runtime deployment is in progress" };
  if (all.some((state) => state === "UNKNOWN"))
    return { status: "UNKNOWN", reason: "Runtime health is not available" };
  if (
    capture === "RUNNING" &&
    deliveryStates.every((state) => state === "RUNNING")
  )
    return { status: "HEALTHY", reason: "All critical components are running" };
  return { status: "UNKNOWN", reason: "Runtime health is not available" };
}

export function connectorTechnology(connector: Connector | null): string {
  if (!connector) return "Not deployed";
  const value = connector.connector_class.toLowerCase();
  if (value.includes("jdbc")) return "Kafka Connect JDBC Sink";
  if (value.includes("elasticsearch")) return "Elasticsearch Sink";
  if (value.includes("clickhouse")) return "ClickHouse Sink";
  if (value.includes("postgres")) return "Debezium PostgreSQL Connector";
  if (value.includes("mysql")) return "Debezium MySQL Connector";
  return connector.connector_class.split(".").at(-1) || "Kafka Connect";
}

export function isSinkConnector(connector: Connector): boolean {
  return connector.connector_type === "sink";
}

export function mapConnectorToDelivery(
  connector: Connector,
  delivery: Omit<Delivery, "connector" | "connector_id">,
): Delivery | null {
  if (!isSinkConnector(connector)) return null;
  return { ...delivery, connector, connector_id: connector.id };
}

export function pipelineSearchText(pipeline: PipelineDetail): string {
  return [
    pipeline.name,
    pipeline.source.name,
    pipeline.kafka_cluster.name,
    ...pipeline.tables.flatMap((table) => [
      table.topic_name,
      `${table.schema_name}.${table.table_name}`,
    ]),
    ...pipeline.destinations.flatMap((delivery) => [
      delivery.name,
      delivery.destination.name,
    ]),
  ]
    .join(" ")
    .toLowerCase();
}
