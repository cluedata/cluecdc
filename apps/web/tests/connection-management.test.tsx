import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Connection } from "@cluecdc/contracts";
import { ConnectionsPage } from "../src/features/connections/components/connection-list";
import { api, post } from "../src/lib/api";

const mocks = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

const savedConnection: Connection = {
  id: "connection-1",
  name: "Saved destination",
  description: "",
  provider: "POSTGRESQL",
  type: "POSTGRESQL",
  category: "DATABASE",
  capabilities: ["DESTINATION"],
  status: "UNKNOWN",
  config: {
    host: "database.internal",
    port: 5432,
    database_name: "analytics",
    username: "writer",
    ssl_enabled: false,
    environment: "DEV",
  },
  credentials: { configured: true, masked_value: "************" },
  last_tested_at: null,
  last_test_status: null,
  last_test_message: null,
  last_checked: null,
  created_at: "2026-10-21T00:00:00Z",
  updated_at: "2026-10-21T00:00:00Z",
};

function renderPage(capability: "SOURCE" | "DESTINATION", rows: Connection[]) {
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === "/session")
      return {
        actor: "admin@example.com",
        email: "admin@example.com",
        role: "Admin",
        environment: "test",
        auth_mode: "session",
        permissions: ["*"],
      };
    if (path.startsWith("/connections?") && !options) return rows;
    if (path === "/jobs/metadata-job")
      return {
        id: "metadata-job",
        status: "COMPLETED",
        error: null,
        result_json: { tables_discovered: 2 },
      };
    if (path === "/connection-providers")
      return [
        {
          provider: "POSTGRESQL",
          category: "DATABASE",
          name: "PostgreSQL",
          description: "PostgreSQL database",
        },
        {
          provider: "AWS_S3",
          category: "OBJECT_STORAGE",
          name: "AWS S3",
          description: "AWS object storage",
        },
      ];
    if (path === "/connections/connection-1" && options?.method === "PUT")
      return savedConnection;
    return {};
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ConnectionsPage capability={capability} />
    </QueryClientProvider>,
  );
}

describe("connection list management", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(post).mockReset();
    mocks.toastSuccess.mockReset();
    mocks.toastError.mockReset();
  });

  it("creates a destination-only connection in a dialog", async () => {
    vi.mocked(post).mockResolvedValue(savedConnection);
    renderPage("DESTINATION", []);

    fireEvent.click(
      await screen.findByRole("button", { name: "New Connection" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Add destination" }),
    ).toBeInTheDocument();
    fireEvent.change(await screen.findByLabelText("Connection name"), {
      target: { value: "Warehouse" },
    });
    fireEvent.change(screen.getByLabelText("Database"), {
      target: { value: "analytics" },
    });
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "writer" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/connections",
        expect.objectContaining({
          name: "Warehouse",
          capabilities: ["DESTINATION"],
        }),
      ),
    );
    expect(
      screen.queryByRole("heading", { name: "Add destination" }),
    ).not.toBeInTheDocument();
  });

  it("uses the source health endpoint and accepts feedback without checks", async () => {
    vi.mocked(post).mockResolvedValue({ status: "HEALTHY" });
    renderPage("SOURCE", [
      { ...savedConnection, name: "Saved source", capabilities: ["SOURCE"] },
    ]);

    fireEvent.click(await screen.findByRole("button", { name: "Test" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/sources/connection-1/test"),
    );
    expect(mocks.toastSuccess).toHaveBeenCalledWith("Connection test passed");
    expect(mocks.toastError).not.toHaveBeenCalled();
  });

  it("loads source metadata from the source list", async () => {
    vi.mocked(post).mockResolvedValue({ id: "metadata-job" });
    renderPage("SOURCE", [
      { ...savedConnection, name: "Saved source", capabilities: ["SOURCE"] },
    ]);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Load metadata for Saved source",
      }),
    );

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/sources/connection-1/discover"),
    );
    expect(api).toHaveBeenCalledWith("/jobs/metadata-job");
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      "2 tables loaded for Saved source",
    );
  });

  it("opens edit and delete dialogs from the destination list", async () => {
    renderPage("DESTINATION", [savedConnection]);

    fireEvent.click(
      await screen.findByRole("button", { name: "Edit Saved destination" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Edit destination" }),
    ).toBeInTheDocument();
    fireEvent.change(await screen.findByLabelText("Connection name"), {
      target: { value: "Renamed destination" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/connections/connection-1",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"capabilities":["DESTINATION"]'),
        }),
      ),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Delete Saved destination" }),
    );
    expect(
      screen.getByRole("heading", { name: "Delete connection?" }),
    ).toBeInTheDocument();
  });
});
