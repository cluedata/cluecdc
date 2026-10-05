import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const env = Object.fromEntries(
  readFileSync(resolve(process.cwd(), "../../.env"), "utf8")
    .split(/\r?\n/)
    .filter((line) => line.includes("="))
    .map((line) => [
      line.slice(0, line.indexOf("=")),
      line.slice(line.indexOf("=") + 1),
    ]),
);

test("MinIO connection and storage-only delivery UI deploy a real S3 sink without rendering credentials", async ({
  page,
  request,
}) => {
  const name = `Browser MinIO ${Date.now()}`;
  const access = env.MINIO_ACCESS_KEY || "cluecdc-test-access";
  const secret = env.MINIO_SECRET_KEY || "cluecdc-test-secret-key";
  const pipeline = (await (await request.get("/api/v1/pipelines")).json()).find(
    (item: { name: string }) => item.name === "Commerce capture",
  );
  let connectionId = "",
    deliveryId = "";
  try {
    await page.goto("/destinations/new");
    await page.getByRole("button", { name: /MinIO/ }).click();
    await page.getByLabel("Connection name", { exact: true }).fill(name);
    await page.getByLabel("Bucket", { exact: true }).fill("cluecdc-cdc");
    await page.getByLabel("Access key", { exact: true }).fill(access);
    await page.getByLabel("Secret key", { exact: true }).fill(secret);
    await expect(
      page.getByLabel("Access key", { exact: true }),
    ).toHaveAttribute("type", "password");
    await expect(page.getByLabel("Database", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Continue to test" }).click();
    const probe = page.waitForResponse(
      (response) =>
        response.url().endsWith("/connections/test") &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Test Connection", exact: true })
      .click();
    expect((await probe).ok()).toBeTruthy();
    await page.getByRole("button", { name: "Review", exact: true }).click();
    expect(await page.locator("body").innerText()).not.toContain(secret);
    expect(await page.locator("body").innerText()).not.toContain(access);
    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith("/connections") &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Create Connection", exact: true })
      .click();
    const response = await created;
    expect(response.ok()).toBeTruthy();
    connectionId = (await response.json()).id;
    await page.goto(
      `/deliveries/new/object-storage?destination_id=${connectionId}`,
    );
    await page
      .getByLabel("Capture pipeline", { exact: true })
      .selectOption(pipeline.id);
    await page.getByRole("checkbox").first().check();
    await expect(
      page.getByLabel("Database schema", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByLabel("Primary key mode", { exact: true }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "Validate", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Deploy delivery", exact: true }),
    ).toBeEnabled();
    await page
      .getByText("Validated connector configuration (redacted)", {
        exact: true,
      })
      .click();
    const rendered = await page.locator("body").innerText();
    expect(rendered).not.toContain(secret);
    expect(rendered).not.toContain(access);
    expect(rendered).not.toContain("ClueDeliveryTransform");
    const deployment = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/destinations/${connectionId}/deploy`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Deploy delivery", exact: true })
      .click();
    const deployed = await deployment;
    expect(deployed.ok()).toBeTruthy();
    deliveryId = (await deployed.json()).id;
    await expect(page).toHaveURL(new RegExp(`/deliveries/${deliveryId}$`));
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${connectionId}/status`)
            ).json()
          ).actual_state,
      )
      .toBe("RUNNING");
  } finally {
    if (deliveryId)
      expect(
        (
          await request.delete(
            `/api/v1/destinations/${connectionId}/deliveries/${deliveryId}`,
          )
        ).ok(),
      ).toBeTruthy();
    if (connectionId)
      expect(
        (await request.delete(`/api/v1/connections/${connectionId}`)).ok(),
      ).toBeTruthy();
  }
});
