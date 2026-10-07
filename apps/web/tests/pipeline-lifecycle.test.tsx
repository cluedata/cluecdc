import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PipelineDetailPage } from "../src/components/pipelines";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => vi.unstubAllGlobals());

const entity = {
  created_at: "2026-09-21T00:00:00Z",
  updated_at: "2026-09-21T00:00:00Z",
};

it("queues an incremental add-table operation from pipeline details", async () => {
  let submitted: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/session")) {
        return Response.json({
          actor: "admin@example.com",
          email: "admin@example.com",
          role: "Admin",
          environment: "test",
          auth_mode: "session",
          permissions: ["*"],
        });
      }
      if (url.endsWith("/pipelines/pipeline-id/status")) {
        return Response.json({
          actual_state: "RUNNING",
          desired_state: "RUNNING",
          connector: { state: "RUNNING", worker_id: "worker" },
          tasks: [{ id: 0, state: "RUNNING", worker_id: "worker" }],
        });
      }
      if (url.includes("/sources/source-id/tables?")) {
        return Response.json([
          {
            ...entity,
            id: "orders-id",
            source_id: "source-id",
            schema_name: "public",
            table_name: "orders",
            primary_key_columns: ["id"],
            estimated_rows: 250,
            estimated_size_bytes: 4096,
            cdc_ready: true,
            cdc_status: "READY",
            cdc_issues: [],
            columns_json: [],
            indexes_json: [],
          },
        ]);
      }
      if (
        url.endsWith("/pipelines/pipeline-id/tables") &&
        options?.method === "POST"
      ) {
        submitted = JSON.parse(String(options.body));
        return Response.json([], { status: 202 });
      }
      if (url.endsWith("/pipelines/pipeline-id")) {
        return Response.json({
          ...entity,
          id: "pipeline-id",
          name: "Commerce capture",
          source_id: "source-id",
          kafka_cluster_id: "kafka-id",
          connect_cluster_id: "connect-id",
          connector_id: "connector-id",
          topic_prefix: "commerce",
          snapshot_mode: "initial",
          desired_state: "RUNNING",
          actual_state: "RUNNING",
          source: {
            ...entity,
            id: "source-id",
            name: "Source PostgreSQL",
            type: "postgresql",
            environment: "DEV",
            host: "source",
            port: 5432,
            database_name: "commerce",
            username: "cdc",
            ssl_enabled: false,
            status: "HEALTHY",
            last_health_check_at: null,
          },
          kafka_cluster: {
            ...entity,
            id: "kafka-id",
            name: "Kafka",
            bootstrap_servers: "kafka:9092",
            security_protocol: "PLAINTEXT",
            status: "HEALTHY",
          },
          connect_cluster: {
            ...entity,
            id: "connect-id",
            name: "Connect",
            base_url: "http://connect:8083",
            kafka_cluster_id: "kafka-id",
            status: "HEALTHY",
          },
          connector: {
            ...entity,
            id: "connector-id",
            name: "cluecdc-commerce",
            actual_state: "RUNNING",
            desired_state: "RUNNING",
            connector_class: "postgres",
            config_json: {},
            runtime_json: {},
            connect_cluster_id: "connect-id",
            connector_type: "source",
          },
          tables: [
            {
              ...entity,
              id: "customers-id",
              pipeline_id: "pipeline-id",
              schema_name: "public",
              table_name: "customers",
              topic_name: "commerce.public.customers",
              primary_key_columns: ["id"],
              destination_schema: "public",
              destination_table: "customers",
              initial_data_strategy: "BACKFILL",
              delete_strategy: "DELETE",
              cdc_status: "STREAMING",
              snapshot_status: "COMPLETED",
              schema_status: "IN_SYNC",
              destination_status: "HEALTHY",
              snapshot_rows_processed: 100,
              snapshot_estimated_rows: 100,
              snapshot_current_chunk: null,
              snapshot_started_at: null,
              snapshot_last_activity_at: null,
              snapshot_finished_at: null,
              removed_at: null,
            },
          ],
          config: {},
          metrics: {
            throughput: null,
            cdc_lag: null,
            snapshot_state: "COMPLETED",
            notice: "Debezium notifications",
          },
          destinations: [],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PipelineDetailPage id="pipeline-id" />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("tab", { name: "Tables" }));
  fireEvent.click(screen.getByRole("button", { name: "Add tables" }));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Add 1 table" }));
  await waitFor(() =>
    expect(submitted).toEqual({
      tables: [
        {
          schema_name: "public",
          table_name: "orders",
          initial_data_strategy: "BACKFILL",
          destination_handling: "AUTO_CREATE",
        },
      ],
    }),
  );
});
