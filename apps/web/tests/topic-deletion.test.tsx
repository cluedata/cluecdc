import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Topic } from "@cluecdc/contracts";
import { DeleteTopicDialog, TopicsPage } from "../src/components/stream";
import { api } from "../src/lib/api";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));
vi.mock("sonner", () => ({ toast: { success: mocks.toastSuccess } }));
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  api: vi.fn(),
}));

const topic: Topic = {
  name: "commerce.orders.v2-test_1",
  partitions: 3,
  replication_factor: 1,
  message_rate: null,
  retention: null,
  size: null,
  partition_details: [],
  internal: false,
  protected: false,
  protection_reason: null,
  used_by_pipelines: [
    {
      id: "pipeline-id",
      name: "Orders pipeline",
      desired_state: "RUNNING",
      actual_state: "RUNNING",
      active: true,
    },
  ],
};

function renderDialog(
  overrides: Partial<React.ComponentProps<typeof DeleteTopicDialog>> = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const props: React.ComponentProps<typeof DeleteTopicDialog> = {
    open: true,
    onOpenChange: vi.fn(),
    topic,
    pending: false,
    error: null,
    onDelete: vi.fn(),
    ...overrides,
  };
  render(
    <QueryClientProvider client={client}>
      <DeleteTopicDialog {...props} />
    </QueryClientProvider>,
  );
  return props;
}

describe("DeleteTopicDialog", () => {
  it("requires the exact full topic name and warns about active pipelines", () => {
    const props = renderDialog();
    const confirm = screen.getByRole("button", { name: "Delete topic" });
    const input = screen.getByRole("textbox", {
      name: `Type “${topic.name}” to confirm`,
    });

    expect(confirm).toBeDisabled();
    expect(screen.getByText(/Orders pipeline/)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: ` ${topic.name}` } });
    expect(confirm).toBeDisabled();
    fireEvent.change(input, { target: { value: topic.name } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(props.onDelete).toHaveBeenCalledTimes(1);
  });

  it("locks destructive and dismissal actions while deletion is pending", () => {
    renderDialog({ pending: true });

    expect(screen.getByRole("button", { name: "Deleting…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("keeps an API error visible in the open dialog", () => {
    const props = renderDialog({ error: new Error("Kafka is unavailable") });

    expect(
      screen.getByText("Kafka is unavailable").closest('[role="alert"]'),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.change(
      screen.getByRole("textbox", {
        name: `Type “${topic.name}” to confirm`,
      }),
      { target: { value: topic.name } },
    );
    const retry = screen.getByRole("button", { name: "Delete topic" });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    expect(props.onDelete).toHaveBeenCalledTimes(1);
  });
});

describe("topic deletion flow", () => {
  it("deletes once, invalidates the flow, and redirects with a success toast", async () => {
    vi.mocked(api).mockReset();
    mocks.replace.mockReset();
    mocks.toastSuccess.mockReset();
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === "/kafka/clusters")
        return [{ id: "cluster-1", name: "Local Kafka" }];
      if (path === "/session") return { permissions: ["kafka.topic.delete"] };
      if (options?.method === "DELETE")
        return { deleted: true, topic: topic.name };
      if (path.startsWith(`/kafka/topics/${encodeURIComponent(topic.name)}`))
        return topic;
      return [];
    });
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={client}>
        <TopicsPage name={topic.name} initialCluster="cluster-1" />
      </QueryClientProvider>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Delete topic" }),
    );
    fireEvent.change(
      screen.getByRole("textbox", {
        name: `Type “${topic.name}” to confirm`,
      }),
      { target: { value: topic.name } },
    );
    const confirm = screen.getByRole("button", { name: "Delete topic" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(mocks.replace).toHaveBeenCalledWith("/kafka/topics"),
    );
    expect(
      vi
        .mocked(api)
        .mock.calls.filter(([, options]) => options?.method === "DELETE"),
    ).toHaveLength(1);
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      `Topic ${topic.name} was deleted`,
    );
  });

  it("disables deletion for a protected internal topic", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/kafka/clusters")
        return [{ id: "cluster-1", name: "Local Kafka" }];
      if (path === "/session") return { permissions: ["kafka.topic.delete"] };
      return {
        ...topic,
        name: "_cluecdc_connect_configs",
        protected: true,
        protection_reason:
          "Kafka Connect internal topics cannot be deleted from ClueCDC",
      };
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <TopicsPage
          name="_cluecdc_connect_configs"
          initialCluster="cluster-1"
        />
      </QueryClientProvider>,
    );

    const action = await screen.findByRole("button", { name: "Delete topic" });
    expect(action).toBeDisabled();
    expect(action).toHaveAttribute(
      "title",
      "Kafka Connect internal topics cannot be deleted from ClueCDC",
    );
  });
});
