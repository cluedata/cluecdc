import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OverviewPage } from "../src/components/overview";

function workspace({
  running = 3,
  degraded = 1,
  failed = 0,
  paused = 1,
  unknown = 0,
  errors = [],
}: {
  running?: number;
  degraded?: number;
  failed?: number;
  paused?: number;
  unknown?: number;
  errors?: Record<string, unknown>[];
} = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, retry: false } },
  });
  client.setQueryData(["overview"], {
    sources: 3,
    healthy_sources: 2,
    pipelines: running + degraded + failed + paused + unknown,
    running,
    degraded,
    failed,
    paused,
    unknown,
    deliveries: 4,
    delivery_running: 3,
    delivery_failed: 0,
    delivery_degraded: 1,
    delivery_paused: 0,
    delivery_unknown: 0,
    destinations: 2,
    destination_running: 3,
    destination_failed: 0,
    destination_degraded: 1,
    destination_paused: 0,
    errors: errors.length,
    throughput: null,
    cdc_lag: null,
    healthy_kafka_clusters: 1,
    kafka_clusters: 1,
    healthy_connect_clusters: 1,
    connect_clusters: 2,
    metrics_notice: "Metrics provider required",
  });
  client.setQueryData(["errors", "recent-active"], errors);
  client.setQueryData(["audit", "meaningful"], []);
  client.setQueryData(
    ["pipelines"],
    [
      {
        id: "pipeline-1",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        name: "Orders CDC",
        source_id: "source-1",
        source_name: "orders-primary",
        kafka_cluster_id: "kafka-1",
        connect_cluster_id: "connect-1",
        connector_id: "connector-1",
        topic_prefix: "orders",
        snapshot_mode: "initial",
        desired_state: "RUNNING",
        actual_state: "RUNNING",
        aggregate_status: "HEALTHY",
        tables: 8,
        destinations: 2,
        throughput: null,
        cdc_lag: null,
      },
    ],
  );
  client.setQueryData(["session"], {
    actor: "admin@example.com",
    email: "admin@example.com",
    role: "Admin",
    environment: "test",
    auth_mode: "session",
    permissions: ["*"],
  });
  return render(
    <QueryClientProvider client={client}>
      <OverviewPage />
    </QueryClientProvider>,
  );
}

describe("operations overview", () => {
  it("summarizes runtime health with clear KPI, flow, and pipeline hierarchy", () => {
    workspace();

    expect(screen.getByText("Total pipelines")).toBeInTheDocument();
    expect(screen.getByText("Data movement")).toBeInTheDocument();
    expect(
      screen.getByRole("list", { name: "Change data capture flow" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "View pipelines" }),
    ).toHaveAttribute("href", "/pipelines");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Orders CDC" })).toHaveAttribute(
      "href",
      "/pipelines/pipeline-1",
    );
  });

  it("turns a zero-pipeline workspace into one focused onboarding experience", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: Infinity, retry: false } },
    });
    client.setQueryData(["overview"], {
      sources: 0,
      healthy_sources: 0,
      pipelines: 0,
      running: 0,
      degraded: 0,
      failed: 0,
      paused: 0,
      unknown: 0,
      deliveries: 0,
      delivery_running: 0,
      delivery_failed: 0,
      delivery_degraded: 0,
      delivery_paused: 0,
      delivery_unknown: 0,
      destinations: 0,
      destination_running: 0,
      destination_failed: 0,
      destination_degraded: 0,
      destination_paused: 0,
      errors: 0,
      throughput: null,
      cdc_lag: null,
      healthy_kafka_clusters: 0,
      kafka_clusters: 0,
      healthy_connect_clusters: 0,
      connect_clusters: 0,
      metrics_notice: "Metrics provider required",
    });
    client.setQueryData(["session"], {
      actor: "admin@example.com",
      email: "admin@example.com",
      role: "Admin",
      environment: "test",
      auth_mode: "session",
      permissions: ["*"],
    });
    render(
      <QueryClientProvider client={client}>
        <OverviewPage />
      </QueryClientProvider>,
    );

    expect(
      screen.getByRole("heading", {
        name: "Build your first reliable data movement path",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Workspace readiness")).toBeInTheDocument();
    expect(screen.queryByText("Requires attention")).not.toBeInTheDocument();
    expect(screen.queryByText("Recent activity")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows infrastructure health as direct drill-down links", () => {
    workspace();
    const systemHealth = screen.getByText("System health").closest("section");
    expect(systemHealth).not.toBeNull();
    expect(
      within(systemHealth as HTMLElement).getByRole("link", {
        name: /Kafka clusters/,
      }),
    ).toHaveAttribute("href", "/kafka/clusters");
    expect(
      within(systemHealth as HTMLElement).getByRole("link", {
        name: /Connect clusters/,
      }),
    ).toHaveAttribute("href", "/connect/clusters");
    expect(
      within(systemHealth as HTMLElement).getAllByText("degraded"),
    ).toHaveLength(3);
  });

  it("does not invent unavailable metrics and routes incidents to their resource", () => {
    const { container } = workspace({
      errors: [
        {
          id: "error-1",
          message: "Capture connector failed",
          category: "CONNECTOR_RUNTIME",
          status: "OPEN",
          pipeline_id: "pipeline-1",
          destination_id: null,
          created_at: new Date().toISOString(),
        },
      ],
    });

    expect(
      screen.getByRole("link", { name: /Capture connector failed/ }),
    ).toHaveAttribute("href", "/pipelines/pipeline-1");
    expect(container.querySelector(".overview-metrics-note")).toHaveTextContent(
      "Live throughput: unavailable · CDC lag: unavailable",
    );
  });
});
