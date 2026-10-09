import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PipelinesPage } from "../src/components/pipelines";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => vi.unstubAllGlobals());

function renderFor(role: "Viewer" | "Ops") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/session")) {
        return Response.json({
          actor: `${role.toLowerCase()}@example.com`,
          email: `${role.toLowerCase()}@example.com`,
          role,
          environment: "test",
          auth_mode: "session",
          permissions:
            role === "Ops"
              ? [
                  "overview.read",
                  "pipelines.read",
                  "pipelines.write",
                  "pipelines.operate",
                  "deliveries.read",
                  "deliveries.write",
                  "sources.read",
                  "destinations.read",
                ]
              : ["overview.read", "pipelines.read", "deliveries.read"],
        });
      }
      if (url.endsWith("/sources") || url.endsWith("/pipelines")) {
        return Response.json([]);
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PipelinesPage />
    </QueryClientProvider>,
  );
}

it("keeps Viewer pipeline inventory read-only", async () => {
  renderFor("Viewer");
  expect(await screen.findByText("No pipelines yet")).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "Create pipeline" }),
  ).not.toBeInTheDocument();
});

it("lets Ops create and operate pipelines", async () => {
  renderFor("Ops");
  const links = await screen.findAllByRole("link", { name: "Create pipeline" });
  expect(links).toHaveLength(2);
  expect(links[0]).toHaveAttribute("href", "/pipelines/new");
});
