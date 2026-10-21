import { test, expect } from "@playwright/test";
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

test("login stays white and readable on desktop/mobile with either saved theme", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/login");
  for (const theme of ["light", "dark"]) {
    await page.evaluate((savedTheme) => {
      localStorage.setItem("cluecdc-theme", savedTheme);
    }, theme);
    for (const viewport of [
      { width: 1440, height: 1000 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.reload();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(
        page.getByRole("button", { name: "Sign in", exact: true }),
      ).toBeVisible();
      for (const selector of ["html", "body", ".auth-shell"]) {
        await expect(page.locator(selector)).toHaveCSS(
          "background-color",
          "rgb(255, 255, 255)",
        );
      }
      await expect(page.locator(".auth-brand-panel")).toHaveCount(0);
      await expect(page.getByRole("form", { name: "Sign in" })).toHaveCount(1);
      await expect(page.locator(".brand-logo-image")).toBeVisible();
      await expect(page.getByLabel("Email", { exact: true })).toHaveCSS(
        "background-color",
        "rgb(255, 255, 255)",
      );
      await expect(page.locator(".auth-brand")).toHaveCSS(
        "color",
        "rgb(16, 33, 59)",
      );
      const colors = await page
        .getByLabel("Email", { exact: true })
        .evaluate((input) => {
          const color = getComputedStyle(input)
            .color.match(/\d+/g)!
            .map(Number);
          const channels = color.map((channel) => {
            const value = channel / 255;
            return value <= 0.04045
              ? value / 12.92
              : ((value + 0.055) / 1.055) ** 2.4;
          });
          const luminance =
            channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
          const card = document
            .querySelector(".auth-card")!
            .getBoundingClientRect();
          return {
            contrast: 1.05 / (luminance + 0.05),
            overflow: document.documentElement.scrollWidth > innerWidth,
            centerX: Math.abs(card.x + card.width / 2 - innerWidth / 2),
            centerY: Math.abs(card.y + card.height / 2 - innerHeight / 2),
          };
        });
      expect(colors.contrast).toBeGreaterThanOrEqual(4.5);
      expect(colors.overflow).toBe(false);
      expect(colors.centerX).toBeLessThanOrEqual(2);
      expect(colors.centerY).toBeLessThanOrEqual(2);
      if (process.env.CLUECDC_UI_SCREENSHOTS === "true") {
        await page.screenshot({
          path: testInfo.outputPath(`login-${theme}-${viewport.width}.png`),
          fullPage: true,
        });
      }
    }
  }
  expect(errors).toEqual([]);
});

test("navigation groups toggle independently, Overview stays visible and User opens user settings", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    localStorage.setItem("cluecdc-sidebar", "expanded"),
  );
  await page.goto("/login");
  await page
    .getByLabel("Email", { exact: true })
    .fill(
      process.env.CLUECDC_UI_EMAIL ||
        fileEnv.DEFAULT_ADMIN_EMAIL ||
        "admin@cluecdc.local",
    );
  await page
    .getByLabel("Password", { exact: true })
    .fill(
      process.env.CLUECDC_UI_PASSWORD ||
        fileEnv.DEFAULT_ADMIN_PASSWORD ||
        "cluecdc-admin",
    );
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/overview$/);
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  for (const name of ["Workspace", "Connections", "Operations", "System"]) {
    const toggle = nav.getByRole("button", { name, exact: true });
    await expect(toggle).toHaveClass("sidebar-group-toggle");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(
      nav.getByRole("link", { name: "Overview", exact: true }),
    ).toBeVisible();
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
  }
  await nav.getByRole("link", { name: "User", exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/users$/);
  await expect(
    nav.getByRole("link", { name: "User", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
  if (process.env.CLUECDC_UI_SCREENSHOTS === "true") {
    await page.screenshot({
      path: testInfo.outputPath("sidebar-user-desktop.png"),
      fullPage: true,
    });
  }
  await page
    .getByRole("button", { name: "Collapse sidebar", exact: true })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Open navigation", exact: true })
    .click();
  await nav.getByRole("button", { name: "System", exact: true }).click();
  await expect(
    nav.getByRole("link", { name: "User", exact: true }),
  ).toHaveCount(0);
  await expect(
    nav.getByRole("link", { name: "Overview", exact: true }),
  ).toBeVisible();
  if (process.env.CLUECDC_UI_SCREENSHOTS === "true") {
    await page.screenshot({
      path: testInfo.outputPath("sidebar-mobile.png"),
      fullPage: true,
    });
  }
  expect(errors).toEqual([]);
});
