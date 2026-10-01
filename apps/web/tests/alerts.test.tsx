import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  AlertDetailPage,
  AlertsPage,
  NotificationChannelsPage,
} from "../src/components/alerts";

function renderWithClient(
  node: React.ReactNode,
  seed: (client: QueryClient) => void,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, retry: false } },
  });
  seed(client);
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

const alert = {
  id: "alert-1",
  fingerprint: "fingerprint",
  event_type: "CONNECT_TASK_FAILED",
  severity: "critical",
  status: "firing",
  source_type: "connector",
  source_id: "connector-1",
  source_name: "orders-sink",
  pipeline_id: "pipeline-1",
  pipeline_name: "orders-cdc",
  component: "kafka-connect",
  title: "Connect Task Failed",
  message: 'relation "orders" does not exist',
  details: { connector: "orders-sink", task_id: 0 },
  first_seen_at: "2026-09-22T03:42:32Z",
  last_seen_at: "2026-09-22T03:44:20Z",
  resolved_at: null,
  acknowledged_at: null,
  acknowledged_by: null,
  silenced_until: null,
  silenced_by: null,
  occurrence_count: 12,
  created_at: "2026-09-22T03:42:32Z",
  updated_at: "2026-09-22T03:44:20Z",
};

describe("alerting UI", () => {
  it("renders active incidents with severity, source, and occurrences", () => {
    renderWithClient(<AlertsPage />, (client) => {
      client.setQueryData(["alerts", "active", "", "", ""], {
        items: [alert],
        page: 1,
        pageSize: 100,
        total: 1,
      });
      client.setQueryData(["pipelines"], []);
    });
    expect(screen.getByText("Connect Task Failed")).toBeInTheDocument();
    expect(screen.getByText("critical")).toBeInTheDocument();
    expect(screen.getByText("orders-sink")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("shows correlated delivery state on alert details", () => {
    renderWithClient(<AlertDetailPage id="alert-1" />, (client) => {
      client.setQueryData(["alert", "alert-1"], {
        ...alert,
        deliveries: [
          {
            id: "delivery-1",
            alert_id: "alert-1",
            channel_id: "channel-1",
            channel_name: "Data Engineering",
            channel_type: "slack",
            kind: "firing",
            status: "sent",
            attempt_count: 1,
            last_error: null,
            next_attempt_at: "2026-09-22T03:42:32Z",
            sent_at: "2026-09-22T03:42:33Z",
            created_at: "2026-09-22T03:42:32Z",
            updated_at: "2026-09-22T03:42:33Z",
          },
        ],
      });
    });
    expect(
      screen.getByText('relation "orders" does not exist'),
    ).toBeInTheDocument();
    expect(screen.getByText("Data Engineering")).toBeInTheDocument();
    expect(screen.getByText("sent")).toBeInTheDocument();
  });

  it("provides a useful empty state before channels are configured", () => {
    renderWithClient(<NotificationChannelsPage />, (client) => {
      client.setQueryData(["notification-channels"], []);
    });
    expect(
      screen.getByText("No notification channels yet"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add notification channel" }),
    ).toBeInTheDocument();
  });
});
