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
  it("summarizes runtime health without embedding the pipeline inventory", () => {
    workspace();

    expect(screen.getByText("Data flow status")).toBeInTheDocument();
    expect(
      screen.getByRole("img", {
        name: "Running: 3, Degraded: 1, Failed: 0, Paused: 1, Unknown: 0",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "View pipelines" }),
    ).toHaveAttribute("href", "/pipelines");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("Live Data Flow")).not.toBeInTheDocument();
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
