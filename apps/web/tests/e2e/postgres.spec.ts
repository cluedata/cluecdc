import { test, expect } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
const root = resolve(process.cwd(), "../..");
test("real PostgreSQL source -> wizard -> Debezium -> Kafka events and lifecycle", async ({
  page,
  request,
}) => {
  const env = Object.fromEntries(
    readFileSync(resolve(root, ".env"), "utf8")
      .split(/\r?\n/)
      .filter((l) => l.includes("="))
      .map((l) => {
        const i = l.indexOf("=");
        return [l.slice(0, i), l.slice(i + 1)];
      }),
  );
  const name = `Browser capture ${Date.now()}`;
  const sourceName = `Browser source ${Date.now()}`;
  const deliveryName = `Browser delivery ${Date.now()}`;
  const prefix = `browser_${Date.now()}`;
  let pipelineId = "";
  let sourceId = "";
  const consoleErrors: string[] = [];
  page.on("pageerror", (e) => consoleErrors.push(e.message));
  // Existing local infrastructure comes from scripts/demo.py. No network routes are mocked.
  try {
    await page.goto("/sources/new");
    await page.getByLabel("Source name").fill(sourceName);
    await page
      .getByLabel("Password", { exact: true })
      .fill(env.SOURCE_PASSWORD);
    const created = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/v1/sources"),
    );
    await page
      .getByRole("button", { name: "Register source", exact: true })
      .click();
    sourceId = (await (await created).json()).id;
    await page.goto(`/sources/${sourceId}`);
    await page.getByRole("button", { name: "Test connection" }).click();
    await expect(
      page.locator(".header-actions").getByText("healthy", { exact: true }),
    ).toBeVisible();
    await page.getByRole("tab", { name: "CDC Readiness" }).click();
    await expect(page.getByText("Wal Level", { exact: true })).toBeVisible();
    await expect(
      page.getByText('Current: "logical"', { exact: false }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Discover tables" }).click();
    await expect(
      page.getByRole("button", { name: "public.customers", exact: true }),
    ).toBeVisible();
    await page.getByRole("tab", { name: "Overview", exact: true }).click();
    await page
      .getByRole("link", { name: "Create pipeline", exact: true })
      .click();
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page
      .getByRole("checkbox", { name: "Capture public.customers" })
      .check();
    await page.getByRole("checkbox", { name: "Capture public.orders" }).check();
    await page.getByLabel("Pipeline name").fill(name);
    await page.getByLabel("Topic prefix").fill(prefix);
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page
      .getByLabel("Kafka cluster", { exact: true })
      .selectOption({ label: "Local Kafka" });
    await page
      .getByLabel("Kafka Connect cluster", { exact: true })
      .selectOption({ label: "Local Connect" });
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    const destinationSelect = page.getByLabel("Destination", { exact: true });
    const reportingDestination = await destinationSelect
      .locator("option")
      .filter({ hasText: "Reporting PostgreSQL" })
      .getAttribute("value");
    expect(reportingDestination).toBeTruthy();
    await destinationSelect.selectOption(reportingDestination!);
    await page.getByLabel("Delivery name").fill(deliveryName);
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(
      page.getByText(`${prefix}.public.customers`, { exact: true }),
    ).toBeVisible();
    await page.getByText("Advanced · Capture configuration").click();
    await expect(page.locator(".json-view")).toContainText("[REDACTED]");
    await expect(page.locator("body")).not.toContainText(env.SOURCE_PASSWORD);
    const createdPipeline = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/v1/pipelines"),
    );
    const deliveryPreview = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/v1\/destinations\/[a-f0-9-]+\/preview$/.test(response.url()),
    );
    const deliveryDeploy = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/v1\/destinations\/[a-f0-9-]+\/deploy$/.test(response.url()),
    );
    await page
      .getByRole("button", { name: "Create Pipeline", exact: true })
      .click();
    pipelineId = (await (await createdPipeline).json()).id;
    const previewResponse = await deliveryPreview;
    const previewPayload = await previewResponse.json();
    expect(previewResponse.ok(), JSON.stringify(previewPayload)).toBeTruthy();
    const deployResponse = await deliveryDeploy;
    const deployPayload = await deployResponse.json();
    expect(deployResponse.ok(), JSON.stringify(deployPayload)).toBeTruthy();
    await expect(page).toHaveURL(/\/pipelines\/[a-f0-9-]+$/);
    await expect(
      page.locator(".header-actions").getByText("healthy", { exact: true }),
    ).toBeVisible();
    execFileSync(
      "docker",
      [
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "compose.test.yaml",
        "exec",
        "-T",
        "cdc-source-postgres",
        "psql",
        "-U",
        "postgres",
        "-d",
        "commerce",
        "-v",
        "ON_ERROR_STOP=1",
      ],
      {
        cwd: root,
        input: readFileSync(resolve(root, "scripts/generate-changes.sql")),
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    await page.getByRole("tab", { name: "Events", exact: true }).click();
    await expect(
      page.getByRole("heading", {
        name: "CDC payloads stay in the data plane",
      }),
    ).toBeVisible();
    await page.getByRole("link", { name: "View topic metadata" }).click();
    await expect(
      page.getByRole("heading", { name: "Topic metadata", exact: true }),
    ).toBeVisible();
    await page.goBack();
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Resume", exact: true }),
    ).toBeVisible();
    await expect(
      page.locator(".header-actions").getByText("paused", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Resume", exact: true }).click();
    await expect(
      page.locator(".header-actions").getByText("healthy", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Restart", exact: true }).click();
    await page.getByRole("tab", { name: "Connector", exact: true }).click();
    await expect(page.getByText("Task 0", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "Activity", exact: true }).click();
    await expect(
      page.getByRole("cell", { name: "pipeline.paused", exact: true }),
    ).toBeVisible();
    expect(consoleErrors).toEqual([]);
  } finally {
    if (pipelineId) {
      const pipelineResponse = await request.get(
        `/api/v1/pipelines/${pipelineId}`,
      );
      if (pipelineResponse.ok()) {
        const pipeline = await pipelineResponse.json();
        for (const delivery of pipeline.destinations)
          await request.delete(
            `/api/v1/destinations/${delivery.destination_id}/deliveries/${delivery.id}`,
          );
        await request.delete(`/api/v1/pipelines/${pipelineId}`);
      }
    }
    if (sourceId) await request.delete(`/api/v1/sources/${sourceId}`);
  }
});
test("overview is usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Data flow status", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "View pipelines", exact: true }),
  ).toBeVisible();
  const overflow = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("body *")]
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          element: `${element.tagName.toLowerCase()}.${element.className}`,
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        };
      })
      .filter(
        ({ left, right, width }) =>
          width > 0 && (left < -1 || right > window.innerWidth + 1),
      )
      .slice(0, 10),
  );
  expect(overflow).toEqual([]);
  mkdirSync(resolve(root, "artifacts"), { recursive: true });
  await page.screenshot({
    path: resolve(root, "artifacts/overview-mobile.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(
    page.getByRole("navigation", { name: "Main navigation" }),
  ).toBeVisible();
});

test("desktop overview displays real pipeline health", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Data flow status", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "View pipelines", exact: true }),
  ).toBeVisible();
  mkdirSync(resolve(root, "artifacts"), { recursive: true });
  await page.screenshot({
    path: resolve(root, "artifacts/overview-desktop.png"),
    fullPage: true,
  });
});

test("legacy events route never fetches CDC payloads", async ({
  page,
  request,
}) => {
  const response = await request.get("/api/v1/events?topic=customers");
  expect(response.status()).toBe(410);
  const payloadRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/events"))
      payloadRequests.push(request.url());
  });
  await page.goto("/events");
  await expect(
    page.getByRole("heading", { name: "CDC payloads stay in the data plane" }),
  ).toBeVisible();
  expect(payloadRequests).toEqual([]);
});
