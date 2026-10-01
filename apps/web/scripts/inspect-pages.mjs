import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import assert from "node:assert/strict";
const origin = process.argv[2] || "http://localhost:3001";
const output = resolve("artifacts", "signal-console", "pages");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const routes = [
    "/overview",
    "/sources",
    "/sources/new",
    "/pipelines",
    "/pipelines/new",
    "/deliveries",
    "/deliveries/new",
    "/destinations",
    "/destinations/new",
    "/kafka/clusters",
    "/kafka/topics",
    "/kafka/consumer-groups",
    "/kafka/connect-clusters",
    "/connect/clusters",
    "/connect/connectors",
    "/data/schemas",
    "/events",
    "/monitoring",
    "/alerts",
    "/logs",
    "/operations/errors",
    "/operations/audit",
    "/settings",
  ];
  for (const width of [1280, 1440, 1600, 1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of routes) {
      await page.goto(origin + route);
      await page
        .locator("main h1")
        .waitFor()
        .catch((error) => {
          throw new Error(
            `${route} did not render a page heading at ${width}px`,
            {
              cause: error,
            },
          );
        });
      await page
        .locator('[role="status"][aria-label="Loading data"]')
        .first()
        .waitFor({ state: "hidden", timeout: 20000 })
        .catch(async (error) => {
          const pending = await page
            .locator('[role="status"][aria-label="Loading data"]')
            .evaluateAll((nodes) =>
              nodes.map((node) => ({
                parent: node.parentElement?.className,
                section: node.parentElement?.parentElement?.className,
              })),
            );
          throw new Error(
            `${route} remained in a loading state at ${width}px: ${JSON.stringify(pending)}`,
            { cause: error },
          );
        });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `${route} overflow at ${width}`,
      );
      await page.screenshot({
        path: resolve(
          output,
          `${route.slice(1).replaceAll("/", "-")}-${width}.png`,
        ),
        fullPage: true,
      });
    }
  }
  assert.deepEqual(errors, []);
  console.log(
    `Inspected ${routes.length} routes at 1280, 1440, 1600, 1920, 1024 and 390px without page errors or overflow.`,
  );
} finally {
  await browser.close();
}
