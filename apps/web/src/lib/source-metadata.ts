import type { Job } from "@cluecdc/contracts";
import { api, post } from "./api";

const POLL_INTERVAL_MS = 1000;
const MAX_POLLS = 180;

export async function loadSourceMetadata(sourceId: string): Promise<Job> {
  const job = await post<Job>(`/sources/${sourceId}/discover`);

  for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
    const current = await api<Job>(`/jobs/${job.id}`);
    if (current.status === "COMPLETED") return current;
    if (current.status === "FAILED") {
      throw new Error(current.error || "Metadata loading failed");
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }

  throw new Error(
    "Metadata loading is still running. Try refreshing the table list shortly.",
  );
}

export function discoveredTableCount(job: Job): number | null {
  if (
    job.result_json &&
    typeof job.result_json === "object" &&
    !Array.isArray(job.result_json)
  ) {
    const value = job.result_json.tables_discovered;
    if (typeof value === "number") return value;
  }
  return null;
}
