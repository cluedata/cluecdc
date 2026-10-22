import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  Connection,
  ConnectionProvider,
  Source,
} from "@cluecdc/contracts";
import { ConnectionTestControl } from "../src/components/connection-test-control";
import { ConnectionWizard } from "../src/features/connections/components/connection-create";
import { ConnectionEditor } from "../src/features/connections/components/connection-edit";
import { SourceEditor } from "../src/features/sources/components/source-editor";
import { api, post } from "../src/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

function wrap(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
});

describe("shared connection test feedback", () => {
  it("shows pending, prevents duplicate tests, and ignores results after inputs change", async () => {
    let finish!: (value: { success: boolean }) => void;
    const run = vi.fn(
      () =>
        new Promise<{ success: boolean }>((resolve) => {
          finish = resolve;
        }),
    );
    const client = new QueryClient();
    const control = (fingerprint: string) => (
      <QueryClientProvider client={client}>
        <ConnectionTestControl fingerprint={fingerprint} run={run} />
      </QueryClientProvider>
    );
    const view = render(control("old endpoint"));
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(
      await screen.findByRole("button", { name: "Testing…" }),
    ).toBeDisabled();
    view.rerender(control("new endpoint"));
    finish({ success: true });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Test connection" }),
      ).toBeEnabled(),
    );
    expect(
      screen.queryByText("Connection test passed"),
    ).not.toBeInTheDocument();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it.each([
    { success: false, checks: [] },
    { status: "failed", checks: [] },
    {
      success: true,
      checks: [{ name: "permission", status: "failed" as const }],
    },
  ])("does not report success for failed checks: %o", async (result) => {
    render(
      wrap(
        <ConnectionTestControl fingerprint="draft" run={async () => result} />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(
      await screen.findByText("Connection test failed"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Connection test passed"),
    ).not.toBeInTheDocument();
  });

  it("displays failed requests and allows retry", async () => {
    const run = vi
      .fn()
      .mockRejectedValueOnce(new Error("Connection unavailable"))
      .mockResolvedValue({ status: "HEALTHY" });
    render(wrap(<ConnectionTestControl fingerprint="draft" run={run} />));
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(
      await screen.findByText("Connection unavailable"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(
      await screen.findByText("Connection test passed"),
    ).toBeInTheDocument();
  });
});

describe("source connection forms", () => {
  function mockProviders() {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/database-providers")
        return [
          {
            type: "postgresql",
            display_name: "PostgreSQL",
            source_supported: true,
            default_port: 5432,
          },
          {
            type: "mysql",
            display_name: "MySQL",
            source_supported: true,
            default_port: 3306,
          },
        ];
      return { status: "HEALTHY" };
    });
  }

  it.each(["postgresql", "mysql"])(
    "tests %s before saving and clears stale success",
    async (type) => {
      mockProviders();
      render(wrap(<SourceEditor open fullPage onOpenChange={vi.fn()} />));
      await screen.findByRole("option", { name: "MySQL" });
      fireEvent.change(screen.getByLabelText("Database type"), {
        target: { value: type },
      });
      fireEvent.change(screen.getByLabelText("Password"), {
        target: { value: "source123" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
      expect(
        await screen.findByText("Connection test passed"),
      ).toBeInTheDocument();
      const request = vi
        .mocked(api)
        .mock.calls.find(([path]) => path === "/sources/test-connection");
      expect(JSON.parse(request![1]!.body as string)).toMatchObject({
        type,
        password: "source123",
      });
      expect(
        vi.mocked(api).mock.calls.some(([path]) => path === "/sources"),
      ).toBe(false);
      fireEvent.change(screen.getByLabelText("Host"), {
        target: { value: "changed.internal" },
      });
      expect(
        screen.queryByText("Connection test passed"),
      ).not.toBeInTheDocument();
    },
  );

  it("validates a new source before testing", async () => {
    mockProviders();
    render(wrap(<SourceEditor open fullPage onOpenChange={vi.fn()} />));
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Password is required")).toBeInTheDocument();
    expect(
      vi
        .mocked(api)
        .mock.calls.some(([path]) => path === "/sources/test-connection"),
    ).toBe(false);
  });

  it("tests edited values with a retained password", async () => {
    mockProviders();
    const source = {
      id: "source-1",
      name: "Existing source",
      type: "postgresql",
      environment: "DEV",
      host: "old.internal",
      port: 5432,
      database_name: "fixture",
      username: "source_user",
      ssl_enabled: false,
      provider_options: {},
    } as Source;
    render(
      wrap(
        <SourceEditor source={source} open fullPage onOpenChange={vi.fn()} />,
      ),
    );
    fireEvent.change(screen.getByLabelText("Host"), {
      target: { value: "edited.internal" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByText("Connection test passed");
    const request = vi
      .mocked(api)
      .mock.calls.find(
        ([path]) => path === "/sources/test-connection?identifier=source-1",
      );
    expect(JSON.parse(request![1]!.body as string)).toMatchObject({
      host: "edited.internal",
    });
    expect(JSON.parse(request![1]!.body as string)).not.toHaveProperty(
      "password",
    );
  });
});

describe("unified connection forms", () => {
  const providers: ConnectionProvider[] = [
    "POSTGRESQL",
    "MYSQL",
    "AWS_S3",
    "MINIO",
  ];
  it.each(providers)(
    "provides a test button in the %s creation form",
    async (provider) => {
      vi.mocked(api).mockResolvedValue(
        providers.map((value) => ({
          provider: value,
          category: ["POSTGRESQL", "MYSQL"].includes(value)
            ? "DATABASE"
            : "OBJECT_STORAGE",
          name: value,
          description: "Fixture provider",
        })),
      );
      vi.mocked(post).mockResolvedValue({ success: true, checks: [] });
      render(wrap(<ConnectionWizard />));
      fireEvent.click(await screen.findByText(provider));
      fireEvent.change(screen.getByLabelText("Connection name"), {
        target: { value: "Draft" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
      await screen.findByText("Connection test passed");
      expect(post).toHaveBeenCalledWith(
        "/connections/test",
        expect.objectContaining({ provider }),
      );
      expect(
        vi.mocked(post).mock.calls.some(([path]) => path === "/connections"),
      ).toBe(false);
    },
  );

  it("keeps the destination capability on the destination creation route", async () => {
    vi.mocked(api).mockResolvedValue([
      {
        provider: "POSTGRESQL",
        category: "DATABASE",
        name: "PostgreSQL",
        description: "Fixture provider",
      },
    ]);
    vi.mocked(post).mockResolvedValue({ id: "connection-1" });
    render(wrap(<ConnectionWizard capability="DESTINATION" />));

    fireEvent.click(await screen.findByText("PostgreSQL"));
    fireEvent.change(screen.getByLabelText("Connection name"), {
      target: { value: "Warehouse" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue to test" }));
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    fireEvent.click(screen.getByRole("button", { name: "Create Connection" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/connections",
        expect.objectContaining({ capabilities: ["DESTINATION"] }),
      ),
    );
  });

  it.each(providers)(
    "provides a test button in the %s edit form",
    async (provider) => {
      vi.mocked(post).mockResolvedValue({ success: true, checks: [] });
      const value = {
        id: "connection-1",
        name: "Saved connection",
        description: "",
        provider,
        type: provider,
        status: "UNKNOWN",
        credentials: { configured: true, masked_value: "************" },
        last_tested_at: null,
        last_test_status: null,
        last_test_message: null,
        last_checked: null,
        created_at: "2026-10-21T00:00:00Z",
        updated_at: "2026-10-21T00:00:00Z",
        category: ["POSTGRESQL", "MYSQL"].includes(provider)
          ? "DATABASE"
          : "OBJECT_STORAGE",
        config: {
          host: "old.internal",
          database_name: "fixture",
          username: "source_user",
          provider_options: { connection_timeout_seconds: 20 },
        },
        capabilities: ["DESTINATION"],
      } as Connection;
      render(wrap(<ConnectionEditor value={value} />));
      fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
      await screen.findByText("Connection test passed");
      expect(post).toHaveBeenCalledWith(
        "/connections/test?identifier=connection-1",
        expect.objectContaining({
          provider,
          credentials: {},
          config: expect.objectContaining({
            provider_options: { connection_timeout_seconds: 20 },
          }),
        }),
      );
      expect(api).not.toHaveBeenCalled();
    },
  );
});
