import { describe, expect, it } from "vitest";
import { captureSchema, sourceSchema, tableMatch } from "../src/lib/validation";
const source = {
  name: "commerce",
  type: "postgresql",
  environment: "dev",
  host: "source",
  port: 5432,
  database_name: "commerce",
  username: "cdc",
  password: "private",
  ssl_enabled: false,
  provider_options: {},
};
const capture = {
  name: "capture",
  topic_prefix: "commerce",
  snapshot_mode: "initial",
  heartbeat_interval_ms: 10000,
  max_batch_size: 2048,
  max_queue_size: 8192,
  poll_interval_ms: 500,
  additional_debezium_properties: {},
};
describe("source and capture validation", () => {
  it("validates PostgreSQL connection fields", () => {
    expect(sourceSchema.safeParse(source).success).toBe(true);
    expect(sourceSchema.safeParse({ ...source, port: 70000 }).success).toBe(
      false,
    );
    expect(
      sourceSchema.safeParse({ ...source, host: "https://source" }).success,
    ).toBe(false);
  });
  it("requires valid topic prefixes and queue capacity", () => {
    expect(captureSchema.safeParse(capture).success).toBe(true);
    expect(
      captureSchema.safeParse({ ...capture, topic_prefix: "invalid.prefix" })
        .success,
    ).toBe(false);
    expect(
      captureSchema.safeParse({ ...capture, max_queue_size: 2048 }).success,
    ).toBe(false);
  });
  it("applies exact include/exclude lists", () => {
    expect(
      tableMatch(
        "public.customers",
        "public.customers,public.orders",
        "public.orders",
      ),
    ).toBe(true);
    expect(tableMatch("public.orders", "", "public.orders")).toBe(false);
    expect(tableMatch("public.payments", "public.orders", "")).toBe(false);
  });
});
