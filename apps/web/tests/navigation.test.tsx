import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Shell } from "../src/components/shell";

const navigationState = vi.hoisted(() => ({
  pathname: "/connect/clusters",
  role: "Admin" as "Admin" | "Ops" | "Viewer",
  permissions: ["*"],
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigationState.pathname,
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("../src/lib/api", () => ({
  ApiError: class ApiError extends Error {
    code = "REQUEST_FAILED";
  },
  api: vi.fn(async (path: string) => {
    if (path === "/session") {
      return {
        actor: "tester",
        role: navigationState.role,
        environment: "test",
        auth_mode: "developer",
        permissions: navigationState.permissions,
      };
    }
    return [];
  }),
  relativeTime: vi.fn(() => "now"),
}));

describe("main navigation", () => {
  beforeEach(() => {
    navigationState.pathname = "/connect/clusters";
    navigationState.role = "Admin";
    navigationState.permissions = ["*"];
  });

  it("groups pipeline-centric resources under clear headings", async () => {
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

    const dataFlow = await screen.findByRole("region", {
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
    const infrastructure = screen.getByRole("region", {
      name: "Infrastructure",
    });
    expect(
      within(infrastructure)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual([
      "Kafka Clusters",
      "Topics",
      "Consumer Groups",
      "Connect Clusters",
    ]);
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

  it.each([
    {
      role: "Viewer" as const,
      permissions: ["overview.read", "pipelines.read", "deliveries.read"],
      visible: ["Overview", "Pipelines", "Deliveries"],
      hidden: ["Sources", "Destinations", "Monitoring", "Settings"],
    },
    {
      role: "Ops" as const,
      permissions: [
        "overview.read",
        "pipelines.read",
        "pipelines.write",
        "deliveries.read",
        "deliveries.write",
        "sources.read",
        "destinations.read",
      ],
      visible: [
        "Overview",
        "Pipelines",
        "Deliveries",
        "Sources",
        "Destinations",
      ],
      hidden: ["Monitoring", "Alerts", "Settings"],
    },
  ])("only shows authorized navigation for $role", async (value) => {
    navigationState.pathname = "/overview";
    navigationState.role = value.role;
    navigationState.permissions = value.permissions;
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

    expect(
      await screen.findByRole("link", { name: "Overview" }),
    ).toBeInTheDocument();
    value.visible.forEach((name) =>
      expect(screen.getByRole("link", { name })).toBeInTheDocument(),
    );
    value.hidden.forEach((name) =>
      expect(screen.queryByRole("link", { name })).not.toBeInTheDocument(),
    );
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
