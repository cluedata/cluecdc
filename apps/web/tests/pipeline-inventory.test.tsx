import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PipelinesPage } from "../src/components/pipelines";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function renderInventory() {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, retry: false } },
  });
  client.setQueryData(
    ["sources"],
    [
      { id: "source-1", name: "Commerce DB" },
      { id: "source-2", name: "Billing DB" },
    ],
  );
  client.setQueryData(
    ["pipelines"],
    [
      {
        id: "pipeline-1",
        name: "Orders capture",
        source_id: "source-1",
        source_name: "Commerce DB",
        kafka_cluster_id: "kafka-1",
        connect_cluster_id: "connect-1",
        connector_id: "connector-1",
        topic_prefix: "orders",
        snapshot_mode: "initial",
        desired_state: "RUNNING",
        actual_state: "RUNNING",
        aggregate_status: "HEALTHY",
        delivery_state: "RUNNING",
        status_reason: "Capture and delivery are healthy",
        tables: 2,
        topics: ["orders.public.orders", "orders.public.items"],
        destinations: 1,
        deliveries_summary: [
          {
            id: "delivery-1",
            name: "Warehouse delivery",
            destination_id: "destination-1",
            destination_name: "Analytics warehouse",
            actual_state: "RUNNING",
          },
        ],
        throughput: null,
        cdc_lag: null,
      },
      {
        id: "pipeline-2",
        name: "Invoices capture",
        source_id: "source-2",
        source_name: "Billing DB",
        kafka_cluster_id: "kafka-1",
        connect_cluster_id: "connect-1",
        connector_id: null,
        topic_prefix: "invoices",
        snapshot_mode: "initial",
        desired_state: "PAUSED",
        actual_state: "PAUSED",
        aggregate_status: "PAUSED",
        delivery_state: "NOT_CONFIGURED",
        tables: 1,
        topics: ["invoices.public.invoices"],
        destinations: 0,
        deliveries_summary: [],
        throughput: null,
        cdc_lag: null,
      },
    ],
  );

  return render(
    <QueryClientProvider client={client}>
      <PipelinesPage />
    </QueryClientProvider>,
  );
}

describe("pipeline inventory", () => {
  it("shows a compact list and keeps flow details in the drill-down", () => {
    const { container } = renderInventory();
    const table = screen.getByRole("table");

    expect(
      within(table).getByText("Orders capture").closest("a"),
    ).toHaveAttribute("href", "/pipelines/pipeline-1");
    expect(within(table).getByText("Analytics warehouse")).toBeInTheDocument();
    expect(container.querySelector(".pipeline-card-flow")).toBeNull();
    expect(screen.queryByText("Last event")).not.toBeInTheDocument();
  });

  it("filters the inventory without exposing detail panels", () => {
    renderInventory();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Search pipelines" }),
      {
        target: { value: "Billing" },
      },
    );

    expect(screen.getByText("Invoices capture")).toBeInTheDocument();
    expect(screen.queryByText("Orders capture")).not.toBeInTheDocument();
    expect(screen.getByText("1 of 2 pipelines")).toBeInTheDocument();
  });
});
