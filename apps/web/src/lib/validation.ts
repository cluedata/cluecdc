import { z } from "zod";
export const sourceSchema = z.object({
  name: z.string().min(1, "Name is required").max(120),
  type: z.enum(["postgresql", "mysql"]),
  environment: z.string().min(1),
  host: z
    .string()
    .min(1, "Host is required")
    .regex(/^[a-zA-Z0-9_.:\-]+$/, "Use a hostname or IP address"),
  port: z.number().int().min(1).max(65535),
  database_name: z.string().min(1, "Database is required"),
  username: z.string().min(1, "Username is required"),
  password: z.string(),
  ssl_enabled: z.boolean(),
  provider_options: z.object({
    server_id: z.number().int().min(1).max(4294967295).optional(),
    connection_timeout_seconds: z.number().int().min(1).max(120).optional(),
    ssl_mode: z.string().optional(),
  }),
});
export type SourceForm = z.infer<typeof sourceSchema>;
export const destinationSchema = sourceSchema.extend({
  environment: z.enum(["DEV", "STAGING", "PROD"]),
  description: z.string().max(1000),
});
export type DestinationForm = z.infer<typeof destinationSchema>;
export const captureSchema = z
  .object({
    name: z.string().min(1, "Pipeline name is required").max(120),
    topic_prefix: z
      .string()
      .min(1)
      .max(100)
      .regex(
        /^[a-zA-Z][a-zA-Z0-9_-]*$/,
        "Start with a letter; use letters, numbers, _ or -",
      ),
    snapshot_mode: z.enum(["initial", "no_data", "never", "always"]),
    heartbeat_interval_ms: z.number().int().min(0).max(3600000),
    max_batch_size: z.number().int().min(1).max(100000),
    max_queue_size: z.number().int().min(2).max(1000000),
    poll_interval_ms: z.number().int().min(100).max(60000),
    additional_debezium_properties: z.record(z.string(), z.string()),
  })
  .refine((v) => v.max_queue_size > v.max_batch_size, {
    message: "Queue size must exceed batch size",
    path: ["max_queue_size"],
  });
export type CaptureForm = z.infer<typeof captureSchema>;
export function tableMatch(name: string, include: string, exclude: string) {
  const includes = include
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const excludes = exclude
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return (
    (!includes.length || includes.includes(name)) && !excludes.includes(name)
  );
}
