import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClustersPage } from "../src/components/infrastructure";
import { api } from "../src/lib/api";

const mocks = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("sonner", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  api: vi.fn(),
}));

function renderPage(kind: "kafka" | "connect") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ClustersPage kind={kind} />
    </QueryClientProvider>,
  );
}

describe("infrastructure connection testing", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    mocks.toastSuccess.mockReset();
    mocks.toastError.mockReset();
  });

  it("tests Kafka form values before saving", async () => {
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/kafka/clusters" && !options) return [];
      if (path === "/kafka/clusters/test")
        return {
          status: "HEALTHY",
          message: "Kafka connection succeeded",
          checked_at: "2026-10-19T00:00:00Z",
          details: { topic_count: 3 },
        };
      return [];
    });
    renderPage("kafka");

    fireEvent.click(screen.getByRole("button", { name: "Register cluster" }));
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(
      await screen.findByText("Kafka connection succeeded"),
    ).toBeInTheDocument();
    expect(vi.mocked(api)).toHaveBeenCalledWith(
      "/kafka/clusters/test",
      expect.objectContaining({ method: "POST" }),
    );
    expect(mocks.toastSuccess).toHaveBeenCalledWith("Connection test passed");
  });

  it("tests Kafka Connect form values and selected Kafka cluster", async () => {
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/connect/clusters" && !options) return [];
      if (path === "/kafka/clusters")
        return [
          {
            id: "kafka-id",
            name: "Local Kafka",
            bootstrap_servers: "kafka:29092",
            status: "HEALTHY",
          },
        ];
      if (path === "/connect/clusters/test")
        return {
          status: "HEALTHY",
          message: "Kafka Connect connection succeeded",
          checked_at: "2026-10-19T00:00:00Z",
          details: { plugin_count: 4 },
        };
      return [];
    });
    renderPage("connect");
    await waitFor(() =>
      expect(vi.mocked(api)).toHaveBeenCalledWith("/kafka/clusters"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Register cluster" }));
    await screen.findByRole("option", { name: "Local Kafka" });
    fireEvent.change(screen.getByLabelText("Kafka cluster"), {
      target: { value: "kafka-id" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(
      await screen.findByText("Kafka Connect connection succeeded"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(vi.mocked(api)).toHaveBeenCalledWith(
        "/connect/clusters/test",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"kafka_cluster_id":"kafka-id"'),
        }),
      ),
    );
  });
});
