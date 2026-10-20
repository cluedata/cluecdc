import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const appOrigin = process.argv[2] || "http://localhost:3001";
const docsOrigin = process.argv[3] || "http://localhost:8001";
const email = process.env.CLUECDC_UI_EMAIL;
const password = process.env.CLUECDC_UI_PASSWORD;
const output = resolve("artifacts", "v0.3.0-brand");

assert(email && password, "Set CLUECDC_UI_EMAIL and CLUECDC_UI_PASSWORD.");
await mkdir(output, { recursive: true });

function assertSquare(box, expected, label) {
  assert(box, `${label} is not visible.`);
  assert(Math.abs(box.width - box.height) < 1, `${label} is distorted.`);
  if (expected !== undefined) {
    assert(
      Math.abs(box.width - expected) < 1,
      `${label} rendered at ${box.width}px instead of ${expected}px.`,
    );
  }
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem("cluecdc-theme", "light");
    localStorage.setItem("cluecdc-sidebar", "expanded");
  });

  await page.goto(`${appOrigin}/login`);
  await page.waitForLoadState("networkidle");
  const loginLogo = page.locator(".auth-brand .brand-logo-image");
  const loginLogoFrame = page.locator(".auth-brand .brand-mark");
  await loginLogo.waitFor();
  assert(
    (await loginLogo.evaluate((image) => image.naturalWidth)) >= 46,
    "Authentication logo did not load at a usable resolution.",
  );
  assertSquare(
    await loginLogoFrame.boundingBox(),
    46,
    "Authentication logo frame",
  );
  assertSquare(await loginLogo.boundingBox(), undefined, "Authentication logo");
  const iconLinks = await page
    .locator('link[rel*="icon"]')
    .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  assert(
    iconLinks.some((href) => href?.includes("/brand/cluecdc-icon.png")),
    "ClueCDC favicon metadata is missing.",
  );
  await page.screenshot({
    path: resolve(output, "login-desktop.png"),
    fullPage: true,
  });

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Overview", exact: true }).waitFor();
  const shellLogo = page.locator(".sidebar .brand-logo-image");
  const shellLogoFrame = page.locator(".sidebar .brand-mark");
  await shellLogo.waitFor();
  assertSquare(
    await shellLogoFrame.boundingBox(),
    38,
    "Expanded sidebar logo frame",
  );
  assertSquare(
    await shellLogo.boundingBox(),
    undefined,
    "Expanded sidebar logo",
  );

  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await page.waitForTimeout(250);
  assertSquare(
    await shellLogoFrame.boundingBox(),
    38,
    "Collapsed sidebar logo frame",
  );
  assertSquare(
    await shellLogo.boundingBox(),
    undefined,
    "Collapsed sidebar logo",
  );
  await page.screenshot({
    path: resolve(output, "sidebar-collapsed.png"),
    fullPage: true,
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await page.waitForTimeout(250);
  await page.getByRole("button", { name: "Open navigation" }).click();
  assertSquare(
    await shellLogoFrame.boundingBox(),
    38,
    "Mobile drawer logo frame",
  );
  assertSquare(await shellLogo.boundingBox(), undefined, "Mobile drawer logo");
  await page.screenshot({
    path: resolve(output, "mobile-drawer.png"),
    fullPage: true,
  });
  assert.deepEqual(errors, []);

  const docs = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await docs.goto(docsOrigin);
  await docs.waitForLoadState("networkidle");
  const docsHeaderLogo = docs.locator(".md-header__button.md-logo img");
  await docsHeaderLogo.waitFor();
  assertSquare(
    await docsHeaderLogo.boundingBox(),
    32,
    "Documentation header logo",
  );
  const docsHeroLogo = docs.locator(".hero-logo");
  await docsHeroLogo.waitFor();
  const heroBox = await docsHeroLogo.boundingBox();
  assert(
    heroBox && heroBox.width > 100,
    "Documentation hero logo is too small.",
  );
  assert(
    Math.abs(heroBox.width - heroBox.height) < 1,
    "Documentation hero logo is distorted.",
  );
  await docs.screenshot({
    path: resolve(output, "documentation-home.png"),
    fullPage: true,
  });
  console.log(
    "Verified ClueCDC logo, favicon, collapsed/mobile shell, authentication, and documentation rendering without distortion.",
  );
} finally {
  await browser.close();
}
