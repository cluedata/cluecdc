import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Status, Field } from "../src/components/common";
import { SourceEditor } from "../src/components/sources";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
describe("important controls", () => {
  it("keeps labels distinct from help text and select options", () => {
    render(
      <Field label="Kafka cluster" hint="Choose a registered cluster">
        <select>
          <option>Local Kafka</option>
        </select>
      </Field>,
    );
    const control = screen.getByRole("combobox", {
      name: "Kafka cluster",
    });
    expect(control).toHaveAccessibleDescription("Choose a registered cluster");
  });
  it("labels unknown runtime state accurately", () => {
    render(<Status value="UNKNOWN" />);
    expect(screen.getByText("unknown")).toHaveClass("status-neutral");
  });
  it("requires credentials before registering a source", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <SourceEditor open onOpenChange={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Register source" }));
    expect(await screen.findByText("Password is required")).toBeInTheDocument();
  });
});
