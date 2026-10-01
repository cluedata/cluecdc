import { describe, expect, it } from "vitest";
import type { Connector, Delivery } from "@cluecdc/contracts";
import {
  derivePipelineHealth,
  mapConnectorToDelivery,
} from "../src/domain/data-flow";

const delivery = (state: string): Pick<Delivery, "actual_state" | "name"> => ({
  name: "analytics-sink",
  actual_state: state,
});

describe("pipeline aggregation", () => {
  it("maps fully running critical components to healthy", () => {
    expect(
      derivePipelineHealth("RUNNING", [delivery("RUNNING")], ["HEALTHY"]),
    ).toEqual({
      status: "HEALTHY",
      reason: "All critical components are running",
    });
  });

  it("maps failed capture or delivery to a failed pipeline", () => {
    expect(derivePipelineHealth("FAILED", [delivery("RUNNING")]).status).toBe(
      "FAILED",
    );
    expect(derivePipelineHealth("RUNNING", [delivery("FAILED")])).toMatchObject(
      {
        status: "FAILED",
        reason: "Delivery analytics-sink failed",
      },
    );
  });

  it("maps excess lag to degraded and paused runtimes to paused", () => {
    expect(
      derivePipelineHealth("RUNNING", [delivery("RUNNING")], [], 6000).status,
    ).toBe("DEGRADED");
    expect(derivePipelineHealth("PAUSED", [delivery("RUNNING")]).status).toBe(
      "PAUSED",
    );
  });
});

describe("delivery adapter", () => {
  const base = {
    id: "connector",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    name: "sink",
    actual_state: "RUNNING",
    desired_state: "RUNNING",
    connector_class: "io.debezium.connector.jdbc.JdbcSinkConnector",
    config_json: {},
    runtime_json: {},
    connect_cluster_id: "connect",
  } as Connector;
  const view = {
    id: "delivery",
    created_at: base.created_at,
    updated_at: base.updated_at,
    destination_id: "destination",
    pipeline_id: "pipeline",
    pipeline_name: "customers",
    name: "analytics",
    delivery_mode: "upsert",
    topic_mapping_json: [],
    configuration_json: {} as Delivery["configuration_json"],
    desired_state: "RUNNING",
    actual_state: "RUNNING",
    destination: {} as Delivery["destination"],
  };

  it("maps only sink connectors to deliveries", () => {
    expect(
      mapConnectorToDelivery({ ...base, connector_type: "sink" }, view)
        ?.connector_id,
    ).toBe("connector");
    expect(
      mapConnectorToDelivery({ ...base, connector_type: "source" }, view),
    ).toBeNull();
  });
});
