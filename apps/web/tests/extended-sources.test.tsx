import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionDialog } from "../src/features/connections/components/connection-dialog";
import { SourceEditor } from "../src/features/sources/components/source-editor";
import { api, post } from "../src/lib/api";

vi.mock("../src/lib/api", async (original) => ({
  ...(await original<typeof import("../src/lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

const providers = [
  ["POSTGRESQL", "PostgreSQL"],
  ["MYSQL", "MySQL"],
  ["MARIADB", "MariaDB"],
  ["SQLSERVER", "SQL Server"],
  ["MONGODB", "MongoDB"],
  ["ORACLE", "Oracle"],
].map(([provider, name]) => ({
  provider,
  name,
  category: "DATABASE",
  capabilities: ["SOURCE"],
  type: provider.toLowerCase(),
  display_name: name,
  source_supported: true,
}));

function wrap(component: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{component}</QueryClientProvider>,
  );
}

describe("extended source forms", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(post).mockReset();
    vi.mocked(api).mockResolvedValue(providers);
    vi.mocked(post).mockResolvedValue({ id: "new-source" });
  });

  it("keeps the PostgreSQL form and exposes all new engines in the source selector", async () => {
    wrap(<SourceEditor fullPage open onOpenChange={vi.fn()} />);
    expect(
      await screen.findByRole("option", { name: "MariaDB" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Host")).toHaveValue("postgres-demo");
    fireEvent.change(screen.getByLabelText("Database type"), {
      target: { value: "mongodb" },
    });
    expect(await screen.findByLabelText(/MongoDB URI/)).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.queryByLabelText("Host")).not.toBeInTheDocument();
  });

  it("sends MongoDB URI in credentials and creates source-only connections", async () => {
    wrap(
      <ConnectionDialog
        open
        value={null}
        capability="SOURCE"
        initialProvider="MONGODB"
        onOpenChange={vi.fn()}
      />,
    );
    fireEvent.change(await screen.findByLabelText("Connection name"), {
      target: { value: "Documents" },
    });
    fireEvent.change(screen.getByLabelText("Database"), {
      target: { value: "inventory" },
    });
    fireEvent.change(screen.getByLabelText(/MongoDB URI/), {
      target: { value: "mongodb+srv://user:private@db/" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/connections",
        expect.objectContaining({
          provider: "MONGODB",
          capabilities: ["SOURCE"],
          config: expect.objectContaining({
            database_name: "inventory",
            ssl_enabled: true,
          }),
          credentials: { connection_uri: "mongodb+srv://user:private@db/" },
        }),
      ),
    );
    const payload = vi.mocked(post).mock.calls[0][1] as { config: unknown };
    expect(JSON.stringify(payload.config)).not.toContain("private");
  });

  it.each([
    ["SQLSERVER", 1433],
    ["MARIADB", 3306],
    ["ORACLE", 1521],
  ] as const)("uses secure %s defaults", async (provider, port) => {
    wrap(
      <ConnectionDialog
        open
        value={null}
        capability="SOURCE"
        initialProvider={provider}
        onOpenChange={vi.fn()}
      />,
    );
    expect(await screen.findByLabelText("Port")).toHaveValue(port);
    expect(screen.getByLabelText("TLS enabled")).toBeChecked();
    expect(screen.getByLabelText("Connection timeout (seconds)")).toHaveValue(
      10,
    );
    if (provider === "ORACLE")
      expect(screen.getByLabelText("Service name")).toBeInTheDocument();
  });
});
