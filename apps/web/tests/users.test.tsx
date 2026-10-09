import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { UsersPage } from "../src/components/users";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../src/lib/api", () => ({
  api: apiMock,
  date: (value: string | null) => value || "Never",
}));

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation(async (path: string) => {
    if (path === "/session") {
      return {
        actor: "admin@example.com",
        email: "admin@example.com",
        role: "Admin",
        environment: "test",
        auth_mode: "session",
        permissions: ["*"],
      };
    }
    if (path === "/users") {
      return [
        {
          id: "admin-id",
          email: "admin@example.com",
          role: "ADMIN",
          status: "ACTIVE",
          last_login_at: null,
          created_at: "2026-10-07T00:00:00Z",
        },
        {
          id: "viewer-id",
          email: "viewer@example.com",
          role: "VIEWER",
          status: "ACTIVE",
          last_login_at: null,
          created_at: "2026-10-08T00:00:00Z",
        },
      ];
    }
    if (path === "/users/invite") {
      return { invite_url: "https://cdc.example.com/invite/one-time-token" };
    }
    return {};
  });
});

it("deletes another user after confirmation", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <UsersPage />
    </QueryClientProvider>,
  );

  await screen.findByText("viewer@example.com");
  expect(
    screen.getByRole("button", { name: "Delete admin@example.com" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Delete viewer@example.com" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Delete user" }));

  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith("/users/viewer-id", {
      method: "DELETE",
    }),
  );
});

it("lists users and creates a copyable invite link", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <UsersPage />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("admin@example.com")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Invite User/i }));
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "ops@example.com" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create Invite" }));
  expect(
    await screen.findByText("https://cdc.example.com/invite/one-time-token"),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith(
      "/users/invite",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});
