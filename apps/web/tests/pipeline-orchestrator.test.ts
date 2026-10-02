import { beforeEach, describe, expect, it, vi } from "vitest";
import { post } from "../src/lib/api";
import { createPipelineFlow } from "../src/services/pipeline-orchestrator";

vi.mock("../src/lib/api", () => ({ post: vi.fn() }));

const capture = {
  name: "customers",
  source_id: "source",
  kafka_cluster_id: "kafka",
  connect_cluster_id: "connect",
  tables: [{ schema_name: "public", table_name: "customers" }],
};
const delivery = {
  name: "customers-sink",
  write_mode: "upsert" as const,
  primary_key_mode: "record_key" as const,
  auto_create: true,
  auto_evolve: true,
  delete_enabled: true,
  null_handling: "ignore" as const,
  batch_size: 500,
  max_retries: 10,
  retry_backoff_ms: 3000,
  tasks_max: 1,
  mappings: [
    {
      topic: "customers.public.customers",
      schema_name: "public",
      table_name: "customers",
    },
  ],
};

describe("pipeline orchestration", () => {
  beforeEach(() => vi.mocked(post).mockReset());

  it("validates, creates and deploys capture before delivery", async () => {
    vi.mocked(post)
      .mockResolvedValueOnce({ config: {}, topics: [] })
      .mockResolvedValueOnce({ id: "pipeline" })
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ topics: [] })
      .mockResolvedValueOnce({ config: {} })
      .mockResolvedValueOnce({ id: "delivery" });
    await createPipelineFlow(capture, "destination", delivery);
    expect(vi.mocked(post).mock.calls.map(([path]) => path)).toEqual([
      "/pipelines/preview",
      "/pipelines",
      "/pipelines/pipeline/deploy",
      "/pipelines/pipeline/prepare-topics",
      "/destinations/destination/preview",
      "/destinations/destination/deploy",
    ]);
  });

  it("reports completed capture work when delivery creation fails", async () => {
    vi.mocked(post)
      .mockResolvedValueOnce({ config: {}, topics: [] })
      .mockResolvedValueOnce({ id: "pipeline" })
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ topics: [] })
      .mockRejectedValueOnce(new Error("sink unavailable"));
    await expect(
      createPipelineFlow(capture, "destination", delivery),
    ).rejects.toMatchObject({
      failedStage: "delivery",
      completed: ["capture_validated", "pipeline_saved", "capture_deployed"],
    });
  });
});
