import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DestinationWizard } from "../src/components/destinations";
import { api, ApiError, post } from "../src/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams("pipeline_id=capture-1"),
}));
vi.mock("../src/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

function renderWizard(
  failInventory = false,
  withTopic = false,
  target = {
    id: "target-1",
    name: "Analytics",
    environment: "PROD",
    type: "postgresql",
    database_name: "analytics",
  },
) {
  vi.mocked(post).mockReset();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/destinations") {
      if (failInventory) throw new Error("Destination inventory unavailable");
      return [target];
    }
    if (path === "/destinations/target-1") return target;
    if (path === "/connections?category=OBJECT_STORAGE&capability=DESTINATION")
      return [
        {
          id: "storage-1",
          name: "MinIO archive",
          type: "MINIO",
          provider: "MINIO",
          category: "OBJECT_STORAGE",
          capabilities: ["DESTINATION"],
        },
      ];
    if (path === "/pipelines/capture-1")
      return {
        id: "capture-1",
        name: "Capture",
        connector: null,
        tables: withTopic
          ? [
              {
                topic_name: "capture.public.customers",
                schema_name: "public",
                table_name: "customers",
                primary_key_columns: ["id"],
              },
              {
                topic_name: "capture.public.cars_unique",
                schema_name: "public",
                table_name: "cars_unique",
                primary_key_columns: [],
                sync_mode: "CURRENT_STATE",
                effective_key_mode: "UNIQUE_INDEX",
                effective_key_columns: ["car_code"],
                effective_key_enforcement: "DATABASE_ENFORCED",
              },
              {
                topic_name: "capture.public.keyless_events",
                schema_name: "public",
                table_name: "keyless_events",
                primary_key_columns: [],
                sync_mode: "CHANGE_EVENT_LOG",
                effective_key_mode: "NONE",
                effective_key_columns: [],
                effective_key_enforcement: null,
              },
            ]
          : [],
      };
    if (path === "/pipelines")
      return [
        { id: "capture-1", name: "Capture", connector_id: "connector-1" },
      ];
    return [];
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(["session"], {
    actor: "admin@example.com",
    email: "admin@example.com",
    role: "Admin",
    environment: "test",
    auth_mode: "session",
    permissions: ["*"],
  });
  render(
    <QueryClientProvider client={client}>
      <DestinationWizard />
    </QueryClientProvider>,
  );
}

it("reuses a registered destination and lets the user switch back to creating one", async () => {
  renderWizard();
  await screen.findByRole("option", { name: /Analytics/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Destination" }), {
    target: { value: "target-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(
    await screen.findByRole("heading", { name: "Add delivery" }),
  ).toBeVisible();
  expect(screen.getByRole("combobox", { name: "Pipeline" })).toHaveValue(
    "capture-1",
  );
  expect(
    screen.queryByLabelText("Password", { exact: true }),
  ).not.toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(screen.getByRole("combobox", { name: "Destination" })).toHaveValue(
    "target-1",
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Destination" }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(screen.getByLabelText("Password", { exact: true })).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Add destination" }),
  ).toBeVisible();
  expect(post).not.toHaveBeenCalled();
});

it("shows inventory failures and keeps the new-destination path available", async () => {
  renderWizard(true);
  const alert = await screen.findByRole("alert");
  expect(
    within(alert).getByText("Destination inventory unavailable"),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(
    screen.getByLabelText("Destination name", { exact: true }),
  ).toBeVisible();
});

it("defaults MySQL mappings to the configured destination database", async () => {
  renderWizard(false, true, {
    id: "target-1",
    name: "Analytics",
    environment: "PROD",
    type: "mysql",
    database_name: "analytics_mysql",
  });
  await screen.findByRole("option", { name: /Analytics/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Destination" }), {
    target: { value: "target-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(
    await screen.findByRole("checkbox", { name: /capture.public.customers/ }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));

  expect(
    screen.getByRole("textbox", {
      name: "Schema for capture.public.customers",
    }),
  ).toHaveValue("analytics_mysql");
});

it("uses effective-key readiness instead of requiring a source primary key", async () => {
  renderWizard(false, true);
  await screen.findByRole("option", { name: /Analytics/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Destination" }), {
    target: { value: "target-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));

  expect(
    await screen.findByRole("checkbox", { name: /capture.public.customers/ }),
  ).toBeEnabled();
  expect(
    screen.getByRole("checkbox", { name: /capture.public.cars_unique/ }),
  ).toBeEnabled();
  expect(screen.getByText(/Unique key: car_code/)).toBeVisible();
  expect(
    screen.getByRole("checkbox", { name: /capture.public.keyless_events/ }),
  ).toBeDisabled();
  expect(
    screen.getByText(
      /Unavailable for database Current State: CHANGE EVENT LOG/,
    ),
  ).toBeVisible();
});

it("uses the same wizard for MinIO and allows keyless event-log topics", async () => {
  renderWizard(false, true);
  vi.mocked(post).mockResolvedValue({ config: {} });
  await screen.findByRole("option", { name: /MinIO archive/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Destination" }), {
    target: { value: "storage-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));

  const keyless = await screen.findByRole("checkbox", {
    name: /capture.public.keyless_events/,
  });
  expect(keyless).toBeEnabled();
  expect(screen.getAllByText(/source key optional/)).toHaveLength(3);
  fireEvent.click(keyless);
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));

  expect(
    screen.getByText(/Table mapping and primary keys are not required/),
  ).toBeVisible();
  expect(screen.queryByLabelText(/Schema for/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));

  expect(screen.getByLabelText("Compression")).toBeVisible();
  expect(screen.getByLabelText("Object key template")).toBeVisible();
  expect(screen.queryByLabelText("Write mode")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Validate and review" }));

  expect(await screen.findByRole("heading", { name: "Review" })).toBeVisible();
  expect(post).toHaveBeenCalledWith(
    "/destinations/storage-1/preview",
    expect.objectContaining({
      delivery_type: "OBJECT_STORAGE",
      topics: ["capture.public.keyless_events"],
      output_format: "JSONL",
    }),
  );
});

it("prepares missing capture topics and retries preview without deploying a delivery", async () => {
  renderWizard(false, true);
  vi.mocked(post)
    .mockRejectedValueOnce(
      new ApiError("TOPIC_NOT_FOUND", "Capture topic is missing", {}, "test"),
    )
    .mockResolvedValueOnce({
      topics: ["capture.public.customers"],
      created: ["capture.public.customers"],
    })
    .mockResolvedValueOnce({ config: {} });
  await screen.findByRole("option", { name: /Analytics/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Destination" }), {
    target: { value: "target-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(
    await screen.findByRole("checkbox", { name: /capture.public.customers/ }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Validate and review" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Prepare capture topics" }),
  );
  expect(
    await screen.findByRole("button", { name: "Deploy destination" }),
  ).toBeVisible();
  expect(vi.mocked(post).mock.calls.map(([path]) => path)).toEqual([
    "/destinations/target-1/preview",
    "/pipelines/capture-1/prepare-topics",
    "/destinations/target-1/preview",
  ]);
});
