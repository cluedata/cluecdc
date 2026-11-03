import { expect, test, type Page } from "@playwright/test";

// Opt-in fixture has its own API/web ports and session accounts. Never runs
// against an operator's shared deployment merely because Playwright was invoked.
test.skip(
  process.env.DQ_BROWSER_TESTS !== "1",
  "Start the isolated Data Quality fixture",
);

async function login(page: Page, role: string) {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(`${role}@dq.test`);
  await page
    .getByLabel("Password", { exact: true })
    .fill("dq-browser-test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/overview$/);
}

test("Admin creates, validates, runs, edits, pauses and resumes an independent check", async ({
  page,
}) => {
  await login(page, "admin");
  await page.getByRole("link", { name: "Data Quality", exact: true }).click();
  await page.getByRole("link", { name: "Create Check", exact: true }).click();
  const name = `Browser quality ${Date.now()}`;
  await page.getByLabel("Check name", { exact: true }).fill(name);
  for (const side of ["Source", "Destination"]) {
    await page
      .getByLabel(`${side} Connection`, { exact: true })
      .selectOption({ label: `DQ Browser ${side} · POSTGRESQL` });
    await page
      .getByLabel(`${side} schema`, { exact: true })
      .selectOption("public");
    await page
      .getByLabel(`${side} table`, { exact: true })
      .selectOption("dq_browser_items");
    await page
      .getByLabel(
        `${side} represents current-state rows with comparable business meaning`,
      )
      .check();
  }
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByLabel("Row Count", { exact: true }).check();
  await page.getByLabel("Schema Match", { exact: true }).check();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByLabel("Scheduled execution", { exact: true }).check();
  await expect(page.getByLabel("Frequency", { exact: true })).toHaveValue(
    "600",
  );
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page
    .getByLabel("Notification channel", { exact: true })
    .selectOption({ label: "DQ Test Channel" });
  await expect(
    page.getByLabel("Alerting enabled", { exact: true }),
  ).toBeChecked();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page
    .getByRole("button", { name: "Validate capabilities", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Capability: SUPPORTED", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save Check", exact: true }).click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  const checkUrl = page.url();
  await expect(
    page.getByText("No associated CDC pipeline", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Run Now", exact: true }).click();
  await expect(page).toHaveURL(/\/data-quality\/runs\//);
  await expect(
    page.getByRole("heading", { name: "Execution: COMPLETED", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Row Count.*PASS/i }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Schema Match.*PASS/i }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Back to Check", exact: true }).click();
  await expect(page.getByRole("link", { name: /MANUAL · v1/ })).toBeVisible();
  await page
    .getByRole("button", { name: "Pause Schedule", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Resume Schedule", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Resume Schedule", exact: true })
    .click();
  await page.getByRole("link", { name: "Edit Check", exact: true }).click();
  await page
    .getByLabel("Description", { exact: true })
    .fill("Edited in authenticated browser");
  for (let i = 0; i < 4; i++)
    await page.getByRole("button", { name: "Next", exact: true }).click();
  await page
    .getByRole("button", { name: "Validate capabilities", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Capability: SUPPORTED", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save Check", exact: true }).click();
  await expect(
    page.getByText("Edited in authenticated browser", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../artifacts/data-quality/browser-admin.png",
    fullPage: true,
  });
  // Restore manual-only fixture state so unattended tests never send notifications.
  await page
    .getByRole("button", { name: "Pause Schedule", exact: true })
    .click();
  expect(page.url()).toBe(checkUrl);
});

test("Viewer reads results but cannot modify or run, including direct routes", async ({
  page,
}) => {
  await login(page, "viewer");
  await page.goto("/data-quality");
  await expect(
    page.getByRole("heading", { name: "Data Quality", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Create Check", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("link", { name: "View Results", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Execution history", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run Now", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Edit Check", exact: true }),
  ).toHaveCount(0);
  const checkPath = new URL(page.url()).pathname.replace(
    "/data-quality/",
    "/api/v1/data-quality/checks/",
  );
  const check = await (await page.request.get(checkPath)).json();
  const response = await page.request.post("/api/v1/data-quality/checks", {
    data: check.configuration,
  });
  expect(response.status()).toBe(403);
  expect((await page.request.post(`${checkPath}/run`)).status()).toBe(403);
  await page.goto("/data-quality/new");
  await expect(page.getByLabel("Check name", { exact: true })).toHaveCount(0);
});

test("Ops edits and runs but cannot delete checks", async ({ page }) => {
  await login(page, "ops");
  await page.goto("/data-quality");
  await expect(
    page.getByRole("link", { name: "Create Check", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "View Results", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/data-quality\/[0-9a-f-]+$/);
  await expect(
    page.getByRole("link", { name: "Edit Check", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Delete Check", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "Edit Check", exact: true }).click();
  await page
    .getByLabel("Description", { exact: true })
    .fill("Edited by Ops in authenticated browser");
  for (let i = 0; i < 4; i++)
    await page.getByRole("button", { name: "Next", exact: true }).click();
  await page
    .getByRole("button", { name: "Validate capabilities", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Capability: SUPPORTED", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save Check", exact: true }).click();
  await expect(
    page.getByText("Edited by Ops in authenticated browser", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Run Now", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Execution: COMPLETED", exact: true }),
  ).toBeVisible();
});
