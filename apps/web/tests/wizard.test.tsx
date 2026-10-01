import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { PipelineWizard } from "../src/components/wizard";
import { api, post } from "../src/lib/api";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("../src/lib/api", () => ({
  api: vi.fn(),
  post: vi.fn(),
  number: (value: number | null) => String(value ?? "Unavailable"),
}));
it("builds a review from selected ready tables", async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/sources")
      return [
        {
          id: "s1",
          name: "Commerce",
          host: "source",
          port: 5432,
          database_name: "commerce",
          status: "HEALTHY",
        },
      ];
    if (path === "/kafka/clusters")
      return [{ id: "k1", name: "Kafka", bootstrap_servers: "kafka:29092" }];
    if (path === "/connect/clusters")
      return [{ id: "c1", name: "Connect", kafka_cluster_id: "k1" }];
    if (path === "/destinations")
      return [
        {
          id: "d1",
          name: "Analytics",
          type: "postgresql",
          database_name: "analytics",
          status: "HEALTHY",
        },
      ];
    if (path.includes("/tables"))
      return [
        {
          id: "t1",
          schema_name: "public",
          table_name: "customers",
          cdc_ready: true,
          cdc_status: "READY",
          primary_key_columns: ["id"],
          cdc_issues: [],
          estimated_rows: 100,
          estimated_size_bytes: 1048576,
        },
        {
          id: "t2",
          schema_name: "public",
          table_name: "bad",
          cdc_ready: true,
          cdc_status: "WARNING",
          primary_key_columns: [],
          cdc_issues: ["No primary key"],
          estimated_rows: 5,
          estimated_size_bytes: 8192,
        },
      ];
    return [];
  });
  vi.mocked(post).mockResolvedValue({
    config: { "database.password": "[REDACTED]" },
    topics: ["commerce.public.customers"],
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <PipelineWizard />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("button", { name: /Continue/ })).toBeDisabled();
  fireEvent.click(await screen.findByRole("radio"));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  const checkbox = await screen.findByRole("checkbox", {
    name: "Capture public.customers",
  });
  expect(
    screen.getByRole("checkbox", { name: "Capture public.bad" }),
  ).toBeEnabled();
  fireEvent.click(checkbox);
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByText("Where should captured events be streamed?");
  fireEvent.change(screen.getByLabelText("Kafka cluster"), {
    target: { value: "k1" },
  });
  fireEvent.change(screen.getByLabelText("Kafka Connect cluster"), {
    target: { value: "c1" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
  await screen.findByText("Where should the data go?");
  fireEvent.change(screen.getByLabelText("Destination"), {
    target: { value: "d1" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/pipelines/preview",
      expect.objectContaining({
        source_id: "s1",
        kafka_cluster_id: "k1",
        connect_cluster_id: "c1",
        tables: [{ schema_name: "public", table_name: "customers" }],
      }),
    ),
  );
  expect(
    await screen.findByRole("heading", { name: "Review your pipeline" }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("Analytics").length).toBeGreaterThan(0);
});
