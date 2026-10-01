import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import assert from "node:assert/strict";
const origin = process.argv[2] || "http://localhost:3000";
const output = resolve("artifacts", "ui-redesign", "live");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const get = async (path) => {
    const r = await page.request.get(origin + "/api/v1" + path);
    assert(r.ok());
    return r.json();
  };
  const pipeline = (await get("/pipelines")).find(
    (p) => p.name === "Commerce capture",
  );
  const targets = await get("/destinations");
  const connectors = await get("/connect/connectors");
  assert(
    pipeline && targets.length >= 2 && connectors.length >= 3,
    "Live acceptance inventory required",
  );
  const paths = [
    "/",
    `/sources/${pipeline.source_id}`,
    `/pipelines/${pipeline.id}`,
    `/destinations/${targets[0].id}`,
    `/kafka/topics/commerce.public.customers?cluster=${pipeline.kafka_cluster_id}`,
  ];
  for (const width of [1024, 1280, 1440, 1920, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (let i = 0; i < paths.length; i++) {
      await page.goto(origin + paths[i]);
      await page.locator("main h1").waitFor();
      await page
        .locator('[role="status"][aria-label="Loading data"]')
        .first()
        .waitFor({ state: "hidden" });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `Detail overflow ${paths[i]} at ${width}`,
      );
      await page.screenshot({
        path: resolve(output, `resource-${i}-${width}.png`),
        fullPage: true,
      });
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(origin + "/sources");
  const table = page.locator(".data-table").first();
  await table.locator("tbody tr").first().waitFor();
  await page.getByRole("button", { name: "Source", exact: true }).click();
  assert.equal(
    await page
      .getByRole("columnheader", { name: "Source", exact: true })
      .getAttribute("aria-sort"),
    "ascending",
  );
  await page.locator(".column-menu summary").click();
  await page
    .getByRole("checkbox", { name: "Environment", exact: true })
    .uncheck();
  assert.equal(
    await page.getByRole("columnheader", { name: /^Environment/ }).count(),
    0,
  );
  await page
    .getByRole("checkbox", { name: "Environment", exact: true })
    .check();
  await page.locator(".column-menu summary").click();
  await table.locator("tbody tr").first().focus();
  await page.keyboard.press("Enter");
  await page.waitForURL("**/sources/*");
  await page.getByRole("tab", { name: "Overview", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await page
    .getByRole("button", { name: "public.customers", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "public.customers", exact: true })
    .click();
  assert(
    await page
      .getByRole("dialog")
      .evaluate((el) => el.classList.contains("detail-drawer")),
  );
  await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("dialog").count(), 0);
  await page.goto(origin + "/data/schemas");
  await page
    .getByRole("button", { name: /Inspect schema/ })
    .first()
    .click();
  await page.getByRole("dialog").waitFor();
  await page.screenshot({ path: resolve(output, "schema-drawer.png") });
  await page.keyboard.press("Escape");
  await page.goto(origin + "/operations/audit");
  await page.locator(".data-table tbody tr").first().focus();
  await page.keyboard.press("Enter");
  await page
    .getByRole("heading", { name: "Changed fields", exact: true })
    .waitFor();
  await page.screenshot({ path: resolve(output, "audit-drawer.png") });
  await page.keyboard.press("Escape");
  await page.goto(origin + "/");
  await page
    .getByRole("button", { name: "Search workspace", exact: true })
    .focus();
  await page.keyboard.press("Control+k");
  await page.getByLabel("Search workspace resources").fill("commerce");
  await page
    .locator(".search-result")
    .filter({ hasText: "Commerce capture" })
    .waitFor();
  assert((await page.locator(".search-result").count()) >= 3);
  await page.screenshot({ path: resolve(output, "search-results.png") });
  await page.keyboard.press("Escape");
  await page.goto(
    origin +
      "/connect/connectors?connector=" +
      encodeURIComponent(connectors[0].name),
  );
  await page
    .getByRole("heading", { name: "Runtime connector state", exact: true })
    .waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    "Verified real detail pages at five widths, table sorting/columns/keyboard navigation, arrow-key tabs, metadata drawers, search results and connector deep links.",
  );
} finally {
  await browser.close();
}
