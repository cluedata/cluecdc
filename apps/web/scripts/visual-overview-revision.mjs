import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const origin = process.argv[2] || "http://localhost:3001";
const email = process.env.CLUECDC_UI_EMAIL;
const password = process.env.CLUECDC_UI_PASSWORD;
const output = resolve("artifacts", "v0.3.0-overview-revision");

assert(email && password, "Set CLUECDC_UI_EMAIL and CLUECDC_UI_PASSWORD.");
await mkdir(output, { recursive: true });

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.addInitScript(() => {
    window.localStorage.setItem("cluecdc-theme", "light");
    window.localStorage.setItem("cluecdc-sidebar", "expanded");
  });
  await page.goto(`${origin}/login`);
  await page.waitForLoadState("networkidle");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Overview", exact: true }).waitFor();
  await page
    .locator('[role="status"][aria-label="Loading data"]')
    .first()
    .waitFor({ state: "hidden", timeout: 20_000 })
    .catch(() => undefined);

  async function assertNoPageOverflow(label) {
    const layout = await page.evaluate(() => ({
      pageWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      mobileQuery: window.matchMedia("(max-width: 850px)").matches,
      mainShellStyle: (() => {
        const element = document.querySelector(".main-shell");
        if (!element) return null;
        const style = getComputedStyle(element);
        return { marginLeft: style.marginLeft, width: style.width };
      })(),
      mainShellRules: (() => {
        const element = document.querySelector(".main-shell");
        const found = [];
        const visit = (rules, media = "all") => {
          for (const rule of rules) {
            if (rule instanceof CSSMediaRule) {
              visit(rule.cssRules, rule.conditionText);
            } else if (
              rule instanceof CSSStyleRule &&
              rule.selectorText.includes("main-shell") &&
              element?.matches(rule.selectorText)
            ) {
              found.push({
                selector: rule.selectorText,
                media,
                matches: media === "all" || matchMedia(media).matches,
                margin: rule.style.getPropertyValue("margin-left"),
                important: rule.style.getPropertyPriority("margin-left"),
                width: rule.style.getPropertyValue("width"),
              });
            }
          }
        };
        for (const sheet of document.styleSheets) {
          try {
            visit(sheet.cssRules);
          } catch {}
        }
        return found;
      })(),
      landmarks: Object.fromEntries(
        [
          ".app-shell",
          ".main-shell",
          ".topbar",
          "main",
          ".workspace-onboarding",
        ].map((selector) => {
          const element = document.querySelector(selector);
          const rect = element?.getBoundingClientRect();
          return [
            selector,
            rect
              ? {
                  left: Math.round(rect.left),
                  right: Math.round(rect.right),
                  width: Math.round(rect.width),
                  scrollWidth: element.scrollWidth,
                }
              : null,
          ];
        }),
      ),
      offenders: [...document.querySelectorAll("body *")]
        .map((element) => ({
          tag: element.tagName.toLowerCase(),
          className:
            typeof element.className === "string" ? element.className : "",
          right: Math.round(element.getBoundingClientRect().right),
        }))
        .filter((element) => element.right > window.innerWidth + 1)
        .slice(0, 8),
    }));
    assert(
      layout.pageWidth <= layout.viewportWidth,
      `${label} has page-level overflow: ${JSON.stringify(layout)}`,
    );
  }

  await assertNoPageOverflow("desktop light");
  await page.screenshot({
    path: resolve(output, "overview-desktop-light.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await assertNoPageOverflow("desktop dark");
  await page.screenshot({
    path: resolve(output, "overview-desktop-dark.png"),
    fullPage: true,
  });

  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 375, height: 812 });
  await page.waitForTimeout(300);
  await assertNoPageOverflow("mobile light");
  await page.screenshot({
    path: resolve(output, "overview-mobile-light.png"),
    fullPage: true,
  });

  const populatedOverview = {
    sources: 3,
    healthy_sources: 3,
    pipelines: 4,
    running: 3,
    degraded: 1,
    failed: 0,
    unknown: 0,
    paused: 0,
    kafka_clusters: 2,
    healthy_kafka_clusters: 2,
    connect_clusters: 2,
    healthy_connect_clusters: 2,
    deliveries: 5,
    delivery_running: 4,
    delivery_failed: 0,
    delivery_degraded: 1,
    delivery_paused: 0,
    delivery_unknown: 0,
    destinations: 3,
    destination_running: 3,
    destination_failed: 0,
    destination_degraded: 0,
    destination_paused: 0,
    errors: 1,
    throughput: null,
    cdc_lag: null,
    metrics_notice: "Metrics provider required",
  };
  const pipelineFixture = [
    ["orders-cdc", "Orders CDC", "orders-primary", 18, 2, "HEALTHY"],
    ["customers-cdc", "Customers CDC", "crm-primary", 7, 1, "HEALTHY"],
    ["inventory-cdc", "Inventory CDC", "warehouse-db", 12, 1, "DEGRADED"],
    ["billing-cdc", "Billing CDC", "billing-primary", 5, 1, "HEALTHY"],
  ].map(([id, name, source_name, tables, destinations, aggregate_status]) => ({
    id,
    name,
    source_name,
    tables,
    destinations,
    aggregate_status,
    source_id: `${id}-source`,
    kafka_cluster_id: "kafka-primary",
    connect_cluster_id: "connect-primary",
    connector_id: `${id}-connector`,
    topic_prefix: id,
    snapshot_mode: "initial",
    desired_state: "RUNNING",
    actual_state: "RUNNING",
    throughput: null,
    cdc_lag: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }));
  await page.route("**/api/v1/monitoring/overview", (route) =>
    route.fulfill({ json: populatedOverview }),
  );
  await page.route("**/api/v1/pipelines", (route) =>
    route.fulfill({ json: pipelineFixture }),
  );
  await page.route("**/api/v1/operations/errors?*", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route("**/api/v1/audit?*", (route) => route.fulfill({ json: [] }));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await page.getByText("Pipeline overview", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Orders CDC" }).waitFor();
  await assertNoPageOverflow("populated desktop light");
  await page.screenshot({
    path: resolve(output, "overview-populated-desktop-light.png"),
    fullPage: true,
  });

  assert.deepEqual(pageErrors, []);
  console.log(
    "Captured empty Overview at 1440px light/dark and 375px light, plus populated desktop, with no page-level overflow or page errors.",
  );
} finally {
  await browser.close();
}
