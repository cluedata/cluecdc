import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const envPath = resolve(process.cwd(), "../../.env");
const fileEnv = Object.fromEntries(
  (existsSync(envPath) ? readFileSync(envPath, "utf8") : "")
    .split(/\r?\n/)
    .filter((line) => line.includes("=") && !line.startsWith("#"))
    .map((line) => [
      line.slice(0, line.indexOf("=")),
      line.slice(line.indexOf("=") + 1),
    ]),
);

function credentials() {
  return {
    email:
      process.env.CLUECDC_UI_EMAIL ||
      process.env.DEFAULT_ADMIN_EMAIL ||
      fileEnv.DEFAULT_ADMIN_EMAIL ||
      "admin@cluecdc.local",
    password:
      process.env.CLUECDC_UI_PASSWORD ||
      process.env.DEFAULT_ADMIN_PASSWORD ||
      fileEnv.DEFAULT_ADMIN_PASSWORD ||
      "cluecdc-admin",
  };
}

export async function signIn(page: Page, request?: APIRequestContext) {
  if (request) {
    const session = await request.get("/api/v1/session");
    if (session.status() === 401) {
      const response = await request.post("/api/v1/auth/login", {
        data: credentials(),
      });
      expect(response.status()).toBe(200);
    } else {
      expect(session.status()).toBe(200);
    }
  }
  await page.goto("/login");
  const button = page.getByRole("button", { name: "Sign in", exact: true });
  if (await button.isVisible()) {
    await page.getByLabel("Email", { exact: true }).fill(credentials().email);
    await page
      .getByLabel("Password", { exact: true })
      .fill(credentials().password);
    await button.click();
  }
  await expect(page).toHaveURL(/\/overview$/);
}
