import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InvitePage, LoginPage } from "../src/components/auth";
import { Shell } from "../src/components/shell";

const navigation = vi.hoisted(() => ({
  path: "/overview",
  replace: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.path,
  useRouter: () => ({
    replace: navigation.replace,
    refresh: navigation.refresh,
  }),
}));

function provider(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  navigation.path = "/overview";
  navigation.replace.mockReset();
  navigation.refresh.mockReset();
});

afterEach(() => vi.unstubAllGlobals());

describe("authentication pages", () => {
  it("signs in with email and password", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ user: { email: "admin@example.com" } }),
      ),
    );
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "admin@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "a long test passphrase" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith("/overview"),
    );
  });

  it("validates and accepts an invite", async () => {
    const fetcher = vi.fn(
      async (input: RequestInfo | URL, options?: RequestInit) => {
        if (options?.method === "POST") {
          return Response.json({ user: { email: "ops@example.com" } });
        }
        return Response.json({
          email: "ops@example.com",
          role: "OPS",
          valid: true,
        });
      },
    );
    vi.stubGlobal("fetch", fetcher);
    render(<InvitePage token="invite-token" />);
    expect(await screen.findByText("ops@example.com")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "a long test passphrase" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "a long test passphrase" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Activate account" }));
    expect(
      await screen.findByText("Your account is ready."),
    ).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("redirects an unauthenticated workspace request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            error: {
              code: "UNAUTHENTICATED",
              message: "Authentication is required",
              details: {},
            },
          },
          { status: 401 },
        ),
      ),
    );
    render(provider(<Shell>Workspace</Shell>));
    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith("/login"),
    );
  });

  it("signs out from the current-user menu", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/logout"))
          return new Response(null, { status: 204 });
        if (url.endsWith("/alerts/summary")) {
          return Response.json({ active_count: 0, recent: [] });
        }
        return Response.json({
          actor: "admin@example.com",
          email: "admin@example.com",
          role: "Admin",
          environment: "test",
          auth_mode: "session",
          permissions: ["*"],
        });
      }),
    );
    render(provider(<Shell>Workspace</Shell>));
    fireEvent.click(
      await screen.findByRole("button", { name: "Current user menu" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith("/login"),
    );
  });
});
