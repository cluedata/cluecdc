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
    window.localStorage.clear();
    navigationState.pathname = "/connect/clusters";
    navigationState.role = "Admin";
    navigationState.permissions = ["*"];
  });

  it("uses the pipeline-centric v0.3 navigation structure", async () => {
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

    const workspace = await screen.findByRole("region", {
      name: "Workspace",
    });
    expect(
      within(workspace)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Pipelines", "Deliveries"]);
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();

    const components = screen.getByRole("region", { name: "Connections" });
    expect(
      within(components)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual([
      "Sources",
      "Destinations",
      "Kafka Clusters",
      "Connect Clusters",
    ]);
    expect(
      within(components).getByRole("link", {
        name: "Connect Clusters",
      }),
    ).toHaveAttribute("aria-current", "page");
    const operations = screen.getByRole("region", { name: "Operations" });
    expect(
      within(operations)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Monitoring", "Alerts", "Audit"]);
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "User" })).toHaveAttribute(
      "href",
      "/settings/users",
    );
    expect(
      screen.queryByRole("link", { name: "API Reference" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Clue workspace")).not.toBeInTheDocument();
  });

  it.each(["Workspace", "Connections", "Operations", "System"])(
    "toggles %s independently while Overview stays visible",
    async (name) => {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      render(
        <QueryClientProvider client={client}>
          <Shell>Workspace</Shell>
        </QueryClientProvider>,
      );
      const group = await screen.findByRole("region", { name });
      const toggle = within(group).getByRole("button", { name });
      expect(toggle).toHaveClass("sidebar-group-toggle");
      const target = document.getElementById(
        toggle.getAttribute("aria-controls")!,
      );
      expect(target).toBeVisible();
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(target).not.toBeVisible();
      expect(screen.getByRole("link", { name: "Overview" })).toBeVisible();
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(target).toBeVisible();
    },
  );

  it("marks only User as current on the users route", async () => {
    navigationState.pathname = "/settings/users";
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <Shell>Users</Shell>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("link", { name: "User" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Settings" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("collapses the connections group and desktop sidebar accessibly", async () => {
    window.localStorage.clear();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { container } = render(
      <QueryClientProvider client={client}>
        <Shell>
          <div>Page content</div>
        </Shell>
      </QueryClientProvider>,
    );

    const connections = await screen.findByRole("button", {
      name: "Connections",
    });
    fireEvent.click(connections);
    expect(connections).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("link", { name: "Sources" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(container.querySelector(".app-shell")).toHaveClass(
      "sidebar-collapsed",
    );
    expect(window.localStorage.getItem("cluecdc-sidebar")).toBe("collapsed");
  });

  it.each([
    {
      role: "Viewer" as const,
      permissions: ["overview.read", "pipelines.read", "deliveries.read"],
      visible: ["Overview", "Pipelines", "Deliveries"],
      hidden: ["Sources", "Destinations", "Monitoring", "Settings", "User"],
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
      hidden: ["Monitoring", "Alerts", "Settings", "User"],
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
