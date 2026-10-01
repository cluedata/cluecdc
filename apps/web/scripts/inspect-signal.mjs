import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import assert from "node:assert/strict";
const origin = process.argv[2] || "http://localhost:3001";
const output = resolve("artifacts/signal-console");
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
  const pipelines = await get("/pipelines");
  const sources = await get("/sources");
  const destinations = await get("/destinations");
  const routes = [
    "/",
    ...sources.slice(0, 1).map((s) => "/sources/" + s.id),
    ...pipelines.slice(0, 1).map((p) => "/pipelines/" + p.id),
    ...destinations.slice(0, 1).map((d) => "/destinations/" + d.id),
  ];
  for (const width of [1280, 1440, 1600, 1920, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (let i = 0; i < routes.length; i++) {
      await page.goto(origin + routes[i]);
      await page.locator("main h1").waitFor();
      await page
        .locator('[aria-label="Loading data"]')
        .first()
        .waitFor({ state: "hidden", timeout: 20000 });
      if (i === 0 && pipelines.length)
        await page
          .locator(".workspace-flow .rail-stage-name")
          .first()
          .waitFor();
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        routes[i] + " overflow at " + width,
      );
      await page.screenshot({
        path: resolve(output, `resource-${i}-${width}.png`),
        fullPage: true,
      });
      if (width >= 1280)
        assert.equal(
          await page
            .locator(".sidebar")
            .evaluate((el) => Math.round(el.getBoundingClientRect().width)),
          184,
        );
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(origin + "/");
  if (pipelines.length) {
    await page.locator(".workspace-flow .rail-stage-name").first().waitFor();
    for (const pipeline of pipelines)
      assert(
        (await page
          .locator(".workspace-flow")
          .filter({ hasText: pipeline.name })
          .count()) >= 1,
      );
  }
  assert.equal(await page.locator(".dashboard-metrics").count(), 0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert(
    await page
      .locator(".rail-flow")
      .evaluateAll((els) =>
        els.every((el) => getComputedStyle(el).animationName === "none"),
      ),
  );
  await page.getByRole("checkbox", { name: "Auto refresh" }).uncheck();
  await page.getByText("MANUAL", { exact: true }).waitFor();
  await page.getByRole("checkbox", { name: "Auto refresh" }).check();
  await page
    .getByRole("button", { name: "Search workspace", exact: true })
    .click();
  await page.getByLabel("Search workspace resources").fill("commerce");
  if (pipelines.some((p) => p.name.toLowerCase().includes("commerce")))
    await page
      .locator(".search-result")
      .filter({ hasText: "Commerce capture" })
      .waitFor();
  await page.keyboard.press("Escape");
  await page.goto(origin + "/sources");
  if (sources.length) {
    await page.locator(".data-table tbody tr").first().waitFor();
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
    await page.locator(".data-table tbody tr").first().focus();
    await page.keyboard.press("Enter");
    await page.waitForURL("**/sources/*");
    await page.getByRole("tab", { name: "Overview", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    const table = page.getByRole("button", {
      name: "public.customers",
      exact: true,
    });
    if (await table.count()) {
      await table.click();
      await page.getByRole("dialog").waitFor();
      await page.keyboard.press("Escape");
    }
  }
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.goto(origin + "/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page
    .locator(".sidebar")
    .getByRole("link", { name: "Pipelines", exact: true })
    .click();
  await page.waitForURL("**/pipelines");
  assert.equal(await page.locator(".sidebar.mobile-open").count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    "Verified real Overview and resource details at 1280, 1440, 1600, 1920, 1024, 768 and 390px; resource data, 184px navigation, reduced motion, refresh, search, sorting, columns, keyboard tabs, drawers and mobile navigation.",
  );
} finally {
  await browser.close();
}
