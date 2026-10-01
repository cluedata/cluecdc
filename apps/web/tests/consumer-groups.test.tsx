import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConsumerGroupsPage } from "../src/components/consumer-groups";

describe("consumer group report", () => {
  it("shows committed offsets and lag for every consumed partition", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: Infinity, retry: false } },
    });
    client.setQueryData(["consumer-groups"], {
      observed_at: "2026-09-24T10:00:00Z",
      clusters: [
        {
          id: "kafka-1",
          name: "Production Kafka",
          status: "HEALTHY",
          group_count: 1,
          error: null,
        },
      ],
      groups: [
        {
          group_id: "cluecdc-delivery-1",
          cluster_id: "kafka-1",
          cluster_name: "Production Kafka",
          state: "Stable",
          protocol_type: "consumer",
          protocol: "range",
          members: 1,
          topics: ["commerce.public.customers"],
          total_lag: 15,
          connector_name: "customers-sink",
          resource_type: "delivery",
          resource_id: "delivery-1",
          resource_name: "Warehouse delivery",
          offsets: [
            {
              topic: "commerce.public.customers",
              partition: 0,
              committed_offset: 90,
              end_offset: 100,
              lag: 10,
            },
            {
              topic: "commerce.public.customers",
              partition: 1,
              committed_offset: 55,
              end_offset: 60,
              lag: 5,
            },
          ],
        },
      ],
    });

    render(
      <QueryClientProvider client={client}>
        <ConsumerGroupsPage />
      </QueryClientProvider>,
    );

    expect(screen.getByText("15")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("cluecdc-delivery-1")).toHaveLength(2);
    expect(
      within(table).getAllByText("commerce.public.customers"),
    ).toHaveLength(2);
    expect(within(table).getByText("90")).toBeInTheDocument();
    expect(within(table).getByText("100")).toBeInTheDocument();
    expect(within(table).getByText("10")).toBeInTheDocument();
    expect(within(table).getByText("55")).toBeInTheDocument();
    expect(within(table).getByText("60")).toBeInTheDocument();
    expect(within(table).getByText("5")).toBeInTheDocument();
    expect(
      within(table).getAllByRole("link", { name: "Warehouse delivery" })[0],
    ).toHaveAttribute("href", "/deliveries/delivery-1");
  });
});
