import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const origin = process.argv[2] || "http://localhost:3001";
const email = process.env.CLUECDC_UI_EMAIL;
const password = process.env.CLUECDC_UI_PASSWORD;
const output = resolve("artifacts", "v0.3.0-ui");
const routes = [
  ["overview", "/overview"],
  ["pipelines", "/pipelines"],
  ["pipeline-wizard", "/pipelines/new"],
  ["deliveries", "/deliveries"],
  ["sources", "/sources"],
  ["destinations", "/destinations"],
  ["monitoring", "/monitoring"],
  ["alerts", "/alerts"],
  ["audit", "/operations/audit"],
  ["settings", "/settings"],
];

assert(email && password, "Set CLUECDC_UI_EMAIL and CLUECDC_UI_PASSWORD.");
await mkdir(output, { recursive: true });

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto(`${origin}/login`);
  await page.waitForLoadState("networkidle");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Overview", exact: true }).waitFor();

  for (const viewport of [
    { name: "desktop", width: 1440, height: 1000 },
    { name: "mobile", width: 375, height: 812 },
  ]) {
    await page.setViewportSize(viewport);
    for (const [name, path] of routes) {
      await page.goto(origin + path);
      await page.locator("main h1").waitFor();
      await page
        .locator('[role="status"][aria-label="Loading data"]')
        .first()
        .waitFor({ state: "hidden", timeout: 20_000 })
        .catch(() => undefined);
      const layout = await page.evaluate(() => ({
        fits: document.documentElement.scrollWidth <= innerWidth,
        pageWidth: document.documentElement.scrollWidth,
        viewportWidth: innerWidth,
        offenders: [...document.querySelectorAll("body *")]
          .map((element) => ({
            tag: element.tagName.toLowerCase(),
            className:
              typeof element.className === "string" ? element.className : "",
            right: Math.round(element.getBoundingClientRect().right),
            width: Math.round(element.getBoundingClientRect().width),
          }))
          .filter((element) => element.right > innerWidth + 1)
          .slice(0, 8),
      }));
      assert(
        layout.fits,
        `${path} has page-level horizontal overflow at ${viewport.width}px: ${JSON.stringify(layout)}`,
      );
      await page.screenshot({
        path: resolve(output, `${name}-${viewport.name}-light.png`),
        fullPage: true,
      });
    }
  }

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${origin}/overview`);
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await page.screenshot({
    path: resolve(output, "overview-desktop-dark.png"),
    fullPage: true,
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`${origin}/pipelines`);
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page
    .getByRole("complementary", { name: "Application sidebar" })
    .waitFor();
  await page.screenshot({
    path: resolve(output, "pipelines-mobile-dark-drawer.png"),
    fullPage: true,
  });

  await page.keyboard.press("Escape");
  assert.deepEqual(errors, []);
  console.log(
    `Captured ${routes.length} routes at desktop/mobile, both themes, with no page-level overflow or page errors.`,
  );
} finally {
  await browser.close();
}
