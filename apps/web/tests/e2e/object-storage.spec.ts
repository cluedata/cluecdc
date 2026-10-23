import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const fileEnv = Object.fromEntries(
  [".env.example", ".env.test.example", ".env"].flatMap((file) =>
    readFileSync(resolve(process.cwd(), "../..", file), "utf8")
      .split(/\r?\n/)
      .filter((line) => line.includes("=") && !line.startsWith("#"))
      .map((line) => [
        line.slice(0, line.indexOf("=")),
        line.slice(line.indexOf("=") + 1),
      ]),
  ),
);
const env = (key: string) => process.env[key] ?? fileEnv[key];

test.beforeEach(async ({ page, request }) => {
  const session = await request.get("/api/v1/session");
  if (session.status() === 401) {
    const response = await request.post("/api/v1/auth/login", {
      data: {
        email: env("DEFAULT_ADMIN_EMAIL"),
        password: env("DEFAULT_ADMIN_PASSWORD"),
      },
    });
    expect(response.status()).toBe(200);

    await page.goto("/login");
    await page
      .getByLabel("Email", { exact: true })
      .fill(env("DEFAULT_ADMIN_EMAIL"));
    await page
      .getByLabel("Password", { exact: true })
      .fill(env("DEFAULT_ADMIN_PASSWORD"));
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/overview$/);
  } else expect(session.status()).toBe(200);
});

test("MinIO connection and storage-only delivery UI deploy a real S3 sink without rendering credentials", async ({
  page,
  request,
}) => {
  const name = `Browser MinIO ${Date.now()}`;
  const access = env("MINIO_ACCESS_KEY") || "minioadmin";
  const secret = env("MINIO_SECRET_KEY") || "minioadmin";
  const pipelines = (await (
    await request.get("/api/v1/pipelines")
  ).json()) as Array<{ id: string; name: string }>;
  const pipeline =
    pipelines.find((item) => item.name === "Commerce capture") ?? pipelines[0];
  expect(
    pipeline,
    "Object-storage delivery test requires at least one capture pipeline",
  ).toBeTruthy();
  let connectionId = "",
    deliveryId = "";
  try {
    await page.goto("/destinations/new");
    await page.getByRole("button", { name: /MinIO/ }).click();
    await page.getByLabel("Connection name", { exact: true }).fill(name);
    await page.getByLabel("Bucket", { exact: true }).fill("cluecdc-cdc");
    await page.getByLabel("Access key", { exact: true }).fill(access);
    await page.getByLabel("Secret key", { exact: true }).fill(secret);
    await expect(
      page.getByLabel("Access key", { exact: true }),
    ).toHaveAttribute("type", "password");
    await expect(page.getByLabel("Database", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Continue to test" }).click();
    const probe = page.waitForResponse(
      (response) =>
        response.url().endsWith("/connections/test") &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Test connection", exact: true })
      .click();
    expect((await probe).ok()).toBeTruthy();
    await page.getByRole("button", { name: "Review", exact: true }).click();
    expect(await page.locator("body").innerText()).not.toContain(secret);
    expect(await page.locator("body").innerText()).not.toContain(access);
    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith("/connections") &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Create Connection", exact: true })
      .click();
    const response = await created;
    expect(response.ok()).toBeTruthy();
    connectionId = (await response.json()).id;
    await page.goto("/deliveries/new");
    await page
      .getByLabel("Destination", { exact: true })
      .selectOption(connectionId);
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page
      .getByLabel("Pipeline", { exact: true })
      .selectOption(pipeline.id);
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(
      page.getByText(/Table mapping and primary keys are not required/),
    ).toBeVisible();
    await expect(
      page.getByLabel("Database schema", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByLabel("Primary key mode", { exact: true }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(page.getByLabel("Compression", { exact: true })).toBeVisible();
    await page
      .getByRole("button", { name: "Validate and review", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Deploy destination", exact: true }),
    ).toBeEnabled();
    await page
      .getByText("Advanced · Generated sink connector configuration", {
        exact: true,
      })
      .click();
    const rendered = await page.locator("body").innerText();
    expect(rendered).not.toContain(secret);
    expect(rendered).not.toContain(access);
    expect(rendered).not.toContain("ClueDeliveryTransform");
    const deployment = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/destinations/${connectionId}/deploy`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Deploy destination", exact: true })
      .click();
    const deployed = await deployment;
    expect(deployed.ok()).toBeTruthy();
    deliveryId = (await deployed.json()).id;
    await expect(page).toHaveURL(new RegExp(`/deliveries/${deliveryId}$`));
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${connectionId}/status`)
            ).json()
          ).actual_state,
      )
      .toBe("RUNNING");
  } finally {
    if (deliveryId)
      expect(
        (
          await request.delete(
            `/api/v1/destinations/${connectionId}/deliveries/${deliveryId}`,
          )
        ).ok(),
      ).toBeTruthy();
    if (connectionId)
      expect(
        (await request.delete(`/api/v1/connections/${connectionId}`)).ok(),
      ).toBeTruthy();
  }
});
