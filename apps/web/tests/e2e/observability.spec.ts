import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

test("real persisted CDC evidence and history match the API", async ({
  page,
  request,
}) => {
  const identifier = process.env.OBSERVABILITY_PIPELINE_ID;
  test.skip(
    !identifier,
    "Requires an owned live observability fixture pipeline",
  );
  const email = process.env.CLUECDC_UI_EMAIL;
  const password = process.env.CLUECDC_UI_PASSWORD;
  expect(email, "Requires explicit owned-fixture credentials").toBeTruthy();
  expect(password, "Requires explicit owned-fixture credentials").toBeTruthy();
  expect(
    (
      await request.post("/api/v1/auth/login", { data: { email, password } })
    ).status(),
  ).toBe(200);
  await page.goto("/login");
  if (
    await page.getByRole("button", { name: "Sign in", exact: true }).isVisible()
  ) {
    await page.getByLabel("Email", { exact: true }).fill(email!);
    await page.getByLabel("Password", { exact: true }).fill(password!);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
  }
  await expect(page).toHaveURL(/\/overview$/);
  const response = await request.get(
    `/api/v1/pipelines/${identifier}/observability`,
  );
  expect(response.ok()).toBeTruthy();
  const evidence = await response.json();
  expect(evidence.capture_lag.status).toBe("AVAILABLE");
  await page.goto(`/pipelines/${identifier}`);
  await page.getByRole("tab", { name: "Observability", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Capture evidence" }),
  ).toBeVisible();
  for (const delivery of evidence.deliveries) {
    await expect(
      page.getByRole("heading", { name: `Delivery: ${delivery.name}` }),
    ).toBeVisible();
  }
  await expect(
    page
      .getByText(/Consumer commits alone do not verify destination writes/)
      .first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Snapshot evidence" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Capture lag history", exact: true }),
  ).toBeVisible();
  await page.getByText("Historical measurements and gaps").click();
  await expect(page.getByText(/explicit sample gaps/)).toBeVisible();
  const output = "../../artifacts/observability/browser";
  await mkdir(output, { recursive: true });
  await page.screenshot({
    path: `${output}/observability-light.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({
    path: `${output}/observability-dark.png`,
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByText("Historical measurements and gaps").click();
  await expect(
    page.getByRole("heading", { name: "Capture evidence" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: `${output}/observability-mobile.png`,
    fullPage: true,
  });
});
