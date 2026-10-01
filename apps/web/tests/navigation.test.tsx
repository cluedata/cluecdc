import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Shell } from "../src/components/shell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/connect/clusters",
}));

vi.mock("../src/lib/api", () => ({
  api: vi.fn(async (path: string) => {
    if (path === "/session") {
      return {
        actor: "tester",
        role: "Admin",
        environment: "test",
        auth_mode: "developer",
      };
    }
    return [];
  }),
}));

describe("main navigation", () => {
  it("groups pipeline-centric resources under clear headings", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <Shell>
          <div>Page content</div>
        </Shell>
      </QueryClientProvider>,
    );

    const dataFlow = screen.getByRole("region", {
      name: "Data Flow",
    });
    expect(
      within(dataFlow)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Pipelines", "Deliveries"]);

    const components = screen.getByRole("region", { name: "Connections" });
    expect(
      within(components)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Sources", "Destinations"]);
    expect(
      screen.queryByText("Lakehouse Destinations"),
    ).not.toBeInTheDocument();

    const infrastructure = screen.getByRole("region", {
      name: "Infrastructure",
    });
    expect(
      within(infrastructure)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Clusters", "Topics", "Consumer Groups", "Connect Clusters"]);
    expect(
      within(infrastructure).getByRole("link", {
        name: "Connect Clusters",
      }),
    ).toHaveAttribute("aria-current", "page");
    const operations = screen.getByRole("region", { name: "Operations" });
    expect(
      within(operations)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Monitoring", "Alerts", "Error Center", "Audit Trail"]);
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "API Reference" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Clue workspace")).not.toBeInTheDocument();
  });

  it("switches between dark and light themes and persists the choice", () => {
    window.localStorage.clear();
    document.documentElement.dataset.theme = "light";
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <Shell>
          <div>Page content</div>
        </Shell>
      </QueryClientProvider>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Switch to dark theme" }),
    );
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("cluecdc-theme")).toBe("dark");
    expect(
      screen.getByRole("button", { name: "Switch to light theme" }),
    ).toBeVisible();

    fireEvent.click(
      screen.getByRole("button", { name: "Switch to light theme" }),
    );
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem("cluecdc-theme")).toBe("light");
  });
});
