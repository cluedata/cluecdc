import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import assert from "node:assert/strict";

const stage = process.argv[2] || "overview";
const origin = process.argv[3] || "http://localhost:3001";
const output = resolve("artifacts", "ui-redesign");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const width of [1024, 1280, 1440, 1920, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto(origin);
    await page
      .getByRole("heading", { name: "Overview", exact: true })
      .waitFor();
    await page.locator(".metric-card").first().waitFor();
    assert.equal(
      await page.locator(".metric-card").first().locator("strong").innerText(),
      "0",
      "Visual inspection must use the clean workspace",
    );
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `Page overflow at ${width}`,
    );
    await page.screenshot({
      path: resolve(output, `${stage}-${width}.png`),
      fullPage: true,
    });
  }
  await page
    .getByRole("button", { name: "Search workspace", exact: true })
    .focus();
  await page.keyboard.press("Control+k");
  await page.getByRole("heading", { name: "Search your workspace" }).waitFor();
  await page.getByLabel("Search workspace resources").fill("commerce");
  await page.getByText("No matching resources", { exact: true }).waitFor();
  await page.screenshot({
    path: resolve(output, `${stage}-search.png`),
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    `Rendered ${stage} at five widths and verified keyboard search against the empty API.`,
  );
} finally {
  await browser.close();
}
