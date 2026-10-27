import { expect, test, type BrowserContext } from "@playwright/test";
import { signIn } from "./auth";

test("admin manages an invited user and UI/API permissions track role and status", async ({
  browser,
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const token = Date.now().toString();
  const email = `qa_${token}@example.test`;
  const password = `Qa-${token}!`;

  const invalidContext = await browser.newContext({
    baseURL: "http://localhost:3000",
  });
  try {
    const invalidPage = await invalidContext.newPage();
    await invalidPage.goto("/login");
    await invalidPage.getByLabel("Email", { exact: true }).fill(email);
    await invalidPage
      .getByLabel("Password", { exact: true })
      .fill("wrong-pass");
    await invalidPage
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await expect(invalidPage.locator(".auth-error")).toHaveText(
      "Invalid email or password.",
    );
  } finally {
    await invalidContext.close();
  }

  await signIn(page, request);
  let userId = "";
  let memberContext: BrowserContext | undefined;
  try {
    await page.goto("/settings/users");
    await page.getByRole("button", { name: "Invite User" }).click();
    const inviteDialog = page.getByRole("dialog", { name: "Invite User" });
    await inviteDialog.getByLabel("Email", { exact: true }).fill(email);
    await inviteDialog.getByLabel("Role", { exact: true }).selectOption("OPS");
    await inviteDialog
      .getByRole("button", { name: "Create Invite", exact: true })
      .click();
    const inviteUrl = await page
      .getByRole("dialog", { name: "Invite created" })
      .locator("code")
      .innerText();
    await page
      .getByRole("dialog", { name: "Invite created" })
      .getByRole("button", { name: "Done", exact: true })
      .click();

    memberContext = await browser.newContext({
      baseURL: "http://localhost:3000",
    });
    const memberPage = await memberContext.newPage();
    await memberPage.goto(inviteUrl);
    await expect(
      memberPage.getByRole("heading", { name: "Create your password" }),
    ).toBeVisible();
    await memberPage.getByLabel("Password", { exact: true }).fill(password);
    await memberPage
      .getByLabel("Confirm password", { exact: true })
      .fill(password);
    await memberPage
      .getByRole("button", { name: "Activate account", exact: true })
      .click();
    await expect(
      memberPage.getByRole("heading", { name: "Your account is ready." }),
    ).toBeVisible();
    await memberPage.getByRole("link", { name: "Sign in" }).click();
    await memberPage.getByLabel("Email", { exact: true }).fill(email);
    await memberPage.getByLabel("Password", { exact: true }).fill(password);
    await memberPage
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await expect(memberPage).toHaveURL(/\/overview$/);
    const memberNav = memberPage.getByRole("navigation", {
      name: "Main navigation",
    });
    await expect(memberNav.getByRole("link", { name: "User" })).toHaveCount(0);
    expect((await memberContext.request.get("/api/v1/users")).status()).toBe(
      403,
    );

    const users = await (await request.get("/api/v1/users")).json();
    userId = users.find((user: { email: string }) => user.email === email).id;
    const row = page.getByRole("row").filter({ hasText: email });
    const changed = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/users/${userId}/role`) &&
        response.request().method() === "PATCH",
    );
    await row.getByLabel(`Role for ${email}`).selectOption("VIEWER");
    expect((await changed).ok()).toBeTruthy();
    expect((await memberContext.request.get("/api/v1/session")).status()).toBe(
      401,
    );
    await memberPage.goto("/login");
    await memberPage.getByLabel("Email", { exact: true }).fill(email);
    await memberPage.getByLabel("Password", { exact: true }).fill(password);
    await memberPage
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await expect(memberPage).toHaveURL(/\/overview$/);

    const pipelines = await (await request.get("/api/v1/pipelines")).json();
    expect(pipelines.length).toBeGreaterThan(0);
    expect(
      (
        await memberContext.request.post(
          `/api/v1/pipelines/${pipelines[0].id}/pause`,
        )
      ).status(),
    ).toBe(403);

    const disabled = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/users/${userId}/disable`) &&
        response.request().method() === "POST",
    );
    await row.getByRole("button", { name: "Disable", exact: true }).click();
    expect((await disabled).ok()).toBeTruthy();
    expect((await memberContext.request.get("/api/v1/session")).status()).toBe(
      401,
    );

    const enabled = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/users/${userId}/enable`) &&
        response.request().method() === "POST",
    );
    await row.getByRole("button", { name: "Enable", exact: true }).click();
    expect((await enabled).ok()).toBeTruthy();

    await row.getByRole("button", { name: `Delete ${email}` }).click();
    await page
      .getByRole("dialog", { name: "Delete user?" })
      .getByRole("button", { name: "Delete user", exact: true })
      .click();
    await expect(page.getByRole("row").filter({ hasText: email })).toHaveCount(
      0,
    );
    userId = "";
  } finally {
    if (memberContext) await memberContext.close();
    if (userId) await request.delete(`/api/v1/users/${userId}`);
  }
});
