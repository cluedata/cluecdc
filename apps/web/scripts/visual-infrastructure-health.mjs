import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const appOrigin = process.argv[2] || "http://localhost:3000";
const email = process.env.CLUECDC_UI_EMAIL;
const password = process.env.CLUECDC_UI_PASSWORD;
const output = resolve("artifacts", "infrastructure-health");

assert(email && password, "Set CLUECDC_UI_EMAIL and CLUECDC_UI_PASSWORD.");
await mkdir(output, { recursive: true });

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto(`${appOrigin}/login`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Overview", exact: true }).waitFor();

  await page.goto(`${appOrigin}/kafka/clusters`);
  await page
    .getByRole("heading", { name: "Kafka clusters", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Edit Local Kafka" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Test connection" }).click();
  await dialog
    .getByText("Kafka connection succeeded", { exact: true })
    .waitFor();
  assert(
    await dialog.getByText("healthy", { exact: true }).isVisible(),
    "Kafka connection result was not healthy.",
  );
  await page.screenshot({
    path: resolve(output, "kafka-test-connection.png"),
    fullPage: true,
  });
  await dialog.getByRole("button", { name: "Cancel" }).click();

  await page.goto(`${appOrigin}/kafka/connect-clusters`);
  await page
    .getByRole("heading", { name: "Kafka Connect clusters", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Edit Local Connect" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Test connection" }).click();
  await dialog
    .getByText("Kafka Connect connection succeeded", { exact: true })
    .waitFor();
  assert(
    await dialog.getByText("healthy", { exact: true }).isVisible(),
    "Kafka Connect result was not healthy.",
  );
  await page.screenshot({
    path: resolve(output, "connect-test-connection.png"),
    fullPage: true,
  });

  assert.deepEqual(errors, []);
  console.log(
    "Verified Kafka and Kafka Connect form tests and healthy results in the production UI.",
  );
} finally {
  await browser.close();
}
