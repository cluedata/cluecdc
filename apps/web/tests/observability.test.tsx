import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Evidence, CaptureTrend } from "../src/components/observability";
import { evidenceText, type MetricEvidence } from "../src/lib/observability";

const missing: MetricEvidence = {
  value: null,
  unit: "records",
  status: "UNAVAILABLE",
  confidence: "UNKNOWN",
  observed_at: null,
  collected_at: "2026-11-10T00:00:00Z",
  source: "kafka_admin",
  reason_code: "OFFSET_MISSING",
};
describe("CDC evidence", () => {
  it.each([
    ["UNAVAILABLE", "Unavailable"],
    ["STALE", "Stale"],
    ["UNSUPPORTED", "Unsupported"],
    ["INCONCLUSIVE", "Insufficient evidence"],
    ["PARTIAL", "Insufficient evidence"],
  ] as const)("renders %s without zero filling", (status, expected) => {
    expect(evidenceText({ ...missing, status })).toBe(expected);
  });
  it("renders a measured zero only when available", () => {
    expect(evidenceText({ ...missing, status: "AVAILABLE", value: 0 })).toBe(
      "0 records",
    );
  });
  it("shows reason, confidence, source and time", () => {
    render(<Evidence metric={missing} />);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(
      screen.getByTitle(/OFFSET_MISSING; kafka_admin; UNKNOWN/),
    ).toBeInTheDocument();
  });
  it("shows empty history as unavailable with explicit gaps", () => {
    render(
      <CaptureTrend
        history={{
          points: [],
          gaps: [
            {
              start: "2026-11-10T00:00:00Z",
              end: "2026-11-10T01:00:00Z",
              reason_code: "SAMPLE_GAP",
            },
          ],
          truncated: false,
        }}
      />,
    );
    expect(screen.getByText(/no measured capture lag/)).toBeInTheDocument();
    expect(screen.getByText(/1 explicit sample gaps/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
