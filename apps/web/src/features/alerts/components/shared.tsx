"use client";

import { useQueryClient } from "@tanstack/react-query";

export const EVENT_TYPES = [
  "CONNECTOR_FAILED",
  "CONNECTOR_UNASSIGNED",
  "CONNECTOR_PAUSED",
  "CONNECTOR_RESTART_LOOP",
  "CONNECT_TASK_FAILED",
  "KAFKA_CONNECT_UNAVAILABLE",
  "SOURCE_CONNECTION_FAILED",
  "SOURCE_DATABASE_UNAVAILABLE",
  "SOURCE_AUTH_FAILED",
  "CDC_REPLICATION_SLOT_ERROR",
  "CDC_BINLOG_ERROR",
  "CDC_WAL_ERROR",
  "CDC_PERMISSION_ERROR",
  "DESTINATION_CONNECTION_FAILED",
  "DESTINATION_AUTH_FAILED",
  "DESTINATION_WRITE_FAILED",
  "DESTINATION_SCHEMA_ERROR",
  "DESTINATION_CONNECTOR_FAILED",
  "PIPELINE_FAILED",
  "PIPELINE_DEGRADED",
  "PIPELINE_NO_EVENTS",
  "PIPELINE_LAG_HIGH",
];

export function invalidateAlerts(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: ["alerts"] });
  client.invalidateQueries({ queryKey: ["alert-summary"] });
}
