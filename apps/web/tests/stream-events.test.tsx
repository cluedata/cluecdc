import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EventsExplorer } from "../src/components/stream";
import { api } from "../src/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  api: vi.fn(),
}));

function renderExplorer() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  client.setQueryData(
    ["kafka-clusters"],
    [{ id: "kafka-id", name: "Local Kafka" }],
  );
  client.setQueryData(
    ["pipelines"],
    [
      {
        id: "pipeline-id",
        name: "PG dev",
        kafka_cluster_id: "kafka-id",
        topic_prefix: "pgdev",
      },
    ],
  );
  client.setQueryData(
    ["topics", "kafka-id"],
    [{ name: "pgdev.public.customers" }],
  );
  render(
    <QueryClientProvider client={client}>
      <EventsExplorer />
    </QueryClientProvider>,
  );
}

describe("Kafka event explorer", () => {
  beforeEach(() => vi.mocked(api).mockReset());

  it("fetches a bounded event sample and opens message details", async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (typeof path !== "string") return [];
      if (path.startsWith("/events?"))
        return {
          events: [
            {
              topic: "pgdev.public.customers",
              partition: 0,
              offset: 42,
              timestamp: 1_760_832_000_000,
              operation: "UPDATE",
              key: { id: 1 },
              before: { id: 1, name: "Old" },
              after: { id: 1, name: "New" },
              changed_fields: ["name"],
              source: { schema: "public", table: "customers" },
              raw: { payload: { op: "u" } },
            },
          ],
          scanned: 1,
          scan_limit: 1000,
          byte_limit: 2_000_000,
          bounded: true,
          notice: "Recent bounded window",
        };
      return [];
    });
    renderExplorer();

    fireEvent.change(await screen.findByLabelText("Kafka cluster"), {
      target: { value: "kafka-id" },
    });
    await screen.findByRole("option", { name: "pgdev.public.customers" });
    fireEvent.change(await screen.findByLabelText("Topic"), {
      target: { value: "pgdev.public.customers" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Fetch recent events" }),
    );

    expect(
      await screen.findByText("1 scanned", { exact: false }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
    expect(
      await screen.findByRole("heading", {
        name: "UPDATE · partition 0 · offset 42",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Changed fields" }),
    ).toBeInTheDocument();
    expect(vi.mocked(api)).toHaveBeenCalledWith(
      expect.stringMatching(
        /^\/events\?cluster_id=kafka-id&topic=pgdev\.public\.customers&limit=100/,
      ),
    );
  });
});
