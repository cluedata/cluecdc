import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChecksPage } from "../src/features/data-quality/pages";
import { CheckEditor } from "../src/features/data-quality/editor";
import {
  defaultRule,
  newConfiguration,
  ruleNames,
} from "../src/features/data-quality/types";

const state = vi.hoisted(() => ({ permissions: ["*"] }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../src/lib/auth", () => ({
  useAuthorization: () => ({
    can: (p: string) =>
      state.permissions.includes("*") || state.permissions.includes(p),
    session: { isLoading: false },
  }),
}));
vi.mock("../src/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  date: () => "Never",
  post: vi.fn(async () => []),
  api: vi.fn(async (path: string) => {
    if (path === "/data-quality/connections")
      return ["source", "destination"].map((side, i) => ({
        id: String(i),
        name: side,
        database: "dq",
        provider: "POSTGRESQL",
        supported: true,
        capabilities: [side.toUpperCase()],
      }));
    if (path.includes("/tables"))
      return { tables: [{ schema_name: "public", table_name: "items" }] };
    if (path.includes("/checks?")) return { items: [], has_more: false };
    return [];
  }),
}));

function mount(content: React.ReactNode) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {content}
    </QueryClientProvider>,
  );
}

describe("Data Quality controls", () => {
  beforeEach(() => {
    state.permissions = ["*"];
  });
  it("has exactly six rule types and safe persisted defaults", () => {
    expect(ruleNames).toHaveLength(6);
    expect(newConfiguration().comparison.scope).toBe("FULL_TABLE");
    expect(newConfiguration().comparison.consistency).toBe("BEST_EFFORT");
    expect(newConfiguration().schedule.frequency_seconds).toBe(600);
    expect(defaultRule("data_age").activity_policy).toBe(
      "ONLY_WHEN_SOURCE_ACTIVE",
    );
  });
  it("shows Viewer an observational empty state without mutation controls", async () => {
    state.permissions = ["data_quality.read"];
    mount(<ChecksPage />);
    expect(
      await screen.findByText("No Data Quality checks"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Create Check" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/does not certify CDC completeness/),
    ).toBeInTheDocument();
  });
  it("requires dataset selection and current-state assertions before showing six rule panels", async () => {
    mount(<CheckEditor />);
    await screen.findByLabelText("Check name");
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Check name"), {
      target: { value: "check" },
    });
    for (const [side, id] of [
      ["Source", "0"],
      ["Destination", "1"],
    ]) {
      fireEvent.change(screen.getByLabelText(`${side} Connection`), {
        target: { value: id },
      });
      const schema = screen.getByLabelText(`${side} schema`);
      await within(schema).findByRole("option", { name: "public" });
      fireEvent.change(schema, { target: { value: "public" } });
      fireEvent.change(screen.getByLabelText(`${side} table`), {
        target: { value: "items" },
      });
      fireEvent.click(
        screen.getByLabelText(
          `${side} represents current-state rows with comparable business meaning`,
        ),
      );
    }
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getAllByRole("checkbox")).toHaveLength(6);
    fireEvent.click(screen.getByLabelText("Data Age", { exact: true }));
    expect(screen.getByLabelText("Activity policy")).toHaveValue(
      "ONLY_WHEN_SOURCE_ACTIVE",
    );
    expect(
      screen.getByText(/Expect New Data treats a stale Destination/),
    ).toBeInTheDocument();
  });
});
