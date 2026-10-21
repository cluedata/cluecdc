import type { ConnectionCheck } from "@cluecdc/contracts";

export type ConnectionTestFeedback = {
  success?: boolean;
  status?: string;
  checks?: ConnectionCheck[];
};

export function connectionTestPassed(result: ConnectionTestFeedback) {
  return (
    result.success !== false &&
    !["failed", "error", "unhealthy", "degraded"].includes(
      result.status?.toLowerCase() || "",
    ) &&
    !result.checks?.some((check) => check.status === "failed")
  );
}
