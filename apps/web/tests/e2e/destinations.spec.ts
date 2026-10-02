import { test, expect } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
const root = resolve(process.cwd(), "../..");
const env = Object.fromEntries(
  readFileSync(resolve(root, ".env"), "utf8")
    .split(/\r?\n/)
    .filter((line) => line.includes("="))
    .map((line) => {
      const i = line.indexOf("=");
      return [line.slice(0, i), line.slice(i + 1)];
    }),
);
function sql(service: string, database: string, statement: string) {
  return execFileSync(
    "docker",
    [
      "compose",
      "-f",
      "compose.yaml",
      "-f",
      "compose.test.yaml",
      "exec",
      "-T",
      service,
      "psql",
      "-U",
      "postgres",
      "-d",
      database,
      "-qAt",
      "-v",
      "ON_ERROR_STOP=1",
    ],
    {
      cwd: root,
      input: statement,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
    },
  ).trim();
}

test("destination wizard delivers real records, mappings and independent lifecycle", async ({
  page,
  request,
}) => {
  test.setTimeout(300000);
  const token = Date.now().toString();
  const table = `browser_customers_${token}`;
  const orders = `browser_orders_${token}`;
  const name = `Browser destination ${token}`;
  const pipelines = await (await request.get("/api/v1/pipelines")).json();
  const pipeline = pipelines.find(
    (value: { name: string }) => value.name === "Commerce capture",
  );
  const customerTopic = `${pipeline.topic_prefix}.public.customers`;
  const ordersTopic = `${pipeline.topic_prefix}.public.orders`;
  let destinationId = "";
  let deliveryId = "";
  let customerId = 0;
  let orderId = 0;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(`/pipelines/${pipeline.id}`);
    await page.getByRole("tab", { name: "Deliveries", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Deliveries" }),
    ).toBeVisible();
    await page.goto(`/deliveries/new?type=DATABASE&pipeline_id=${pipeline.id}`);
    await expect(page.getByRole("button", { name: /MySQL/ })).toBeEnabled();
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.getByLabel("Destination name", { exact: true }).fill(name);
    await page.getByLabel("Host", { exact: true }).fill("destination-postgres");
    await page.getByLabel("Database", { exact: true }).fill("analytics");
    await page.getByLabel("Username", { exact: true }).fill("delivery_user");
    await page
      .getByLabel("Password", { exact: true })
      .fill(env.DESTINATION_PASSWORD);
    await page
      .getByRole("button", { name: "Test connection", exact: true })
      .click();
    await expect(
      page.getByText("Destination connection succeeded", { exact: true }),
    ).toBeVisible();
    const created = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/v1/destinations"),
    );
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    destinationId = (await (await created).json()).id;
    await expect(page.getByLabel("Pipeline", { exact: true })).toHaveValue(
      pipeline.id,
    );
    await page
      .getByRole("checkbox", {
        name: new RegExp(customerTopic.replaceAll(".", "\\.")),
      })
      .check();
    await page
      .getByRole("checkbox", {
        name: new RegExp(ordersTopic.replaceAll(".", "\\.")),
      })
      .check();
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page
      .getByRole("checkbox", { name: "Use topic table name automatically" })
      .uncheck();
    await page
      .getByLabel(`Schema for ${customerTopic}`, { exact: true })
      .fill("analytics");
    await page
      .getByLabel(`Table for ${customerTopic}`, { exact: true })
      .fill(table);
    await page
      .getByLabel(`Schema for ${ordersTopic}`, { exact: true })
      .fill("analytics");
    await page
      .getByLabel(`Table for ${ordersTopic}`, { exact: true })
      .fill(orders);
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page
      .getByLabel("Auto evolve schema", { exact: true })
      .selectOption("off");
    await page
      .getByRole("button", { name: "Validate and review", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Deploy destination", exact: true }),
    ).toBeVisible();
    await page
      .getByText("Advanced · Generated sink connector configuration", {
        exact: true,
      })
      .click();
    await expect(page.locator(".json-view")).toContainText(
      '"connection.password": "[REDACTED]"',
    );
    expect(
      (await page.locator("body").innerText()).includes(
        env.DESTINATION_PASSWORD,
      ),
    ).toBe(false);
    mkdirSync(resolve(root, "artifacts"), { recursive: true });
    await page.screenshot({
      path: resolve(root, "artifacts/destination-review.png"),
      fullPage: true,
    });
    const deployed = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/destinations/${destinationId}/deploy`),
    );
    await page
      .getByRole("button", { name: "Deploy destination", exact: true })
      .click();
    deliveryId = (await (await deployed).json()).id;
    await expect(page).toHaveURL(new RegExp(`/deliveries/${deliveryId}$`));
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${destinationId}/status`)
            ).json()
          ).actual_state,
        { timeout: 30_000 },
      )
      .toBe("RUNNING");
    customerId = Number(
      sql(
        "cdc-source-postgres",
        "commerce",
        `INSERT INTO customers(name,email) VALUES ('Browser delivery ${token}','browser-${token}@example.test') RETURNING id;`,
      ),
    );
    orderId = Number(
      sql(
        "cdc-source-postgres",
        "commerce",
        `INSERT INTO orders(customer_id,total) VALUES (${customerId},12.34) RETURNING id;`,
      ),
    );
    await expect
      .poll(() =>
        sql(
          "destination-postgres",
          "analytics",
          `SELECT name FROM analytics.${table} WHERE id=${customerId};`,
        ),
      )
      .toBe(`Browser delivery ${token}`);
    await expect
      .poll(() =>
        sql(
          "destination-postgres",
          "analytics",
          `SELECT total FROM analytics.${orders} WHERE id=${orderId};`,
        ),
      )
      .toBe("12.34");
    sql(
      "cdc-source-postgres",
      "commerce",
      `UPDATE customers SET name='Browser delivery ${token} updated',updated_at=now() WHERE id=${customerId};`,
    );
    await expect
      .poll(() =>
        sql(
          "destination-postgres",
          "analytics",
          `SELECT name FROM analytics.${table} WHERE id=${customerId};`,
        ),
      )
      .toBe(`Browser delivery ${token} updated`);
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${destinationId}/status`)
            ).json()
          ).actual_state,
      )
      .toBe("PAUSED");
    expect(
      (
        await (
          await request.get(`/api/v1/pipelines/${pipeline.id}/status`)
        ).json()
      ).desired_state,
    ).toBe("RUNNING");
    await expect(
      page.getByRole("button", { name: "Resume", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Resume", exact: true }).click();
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${destinationId}/status`)
            ).json()
          ).actual_state,
        { timeout: 30_000 },
      )
      .toBe("RUNNING");
    await page.getByRole("button", { name: "Restart", exact: true }).click();
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${destinationId}/status`)
            ).json()
          ).actual_state,
      )
      .toBe("RUNNING");
    await page.getByRole("tab", { name: "Tasks", exact: true }).click();
    await expect(
      page.getByText("running", { exact: true }).first(),
    ).toBeVisible();
    await page.screenshot({
      path: resolve(root, "artifacts/destination-connector.png"),
      fullPage: true,
    });
    await page.getByRole("tab", { name: "Mapping", exact: true }).click();
    await expect(
      page.getByText(`analytics.${table}`, { exact: true }),
    ).toBeVisible();
    await page.getByRole("tab", { name: "History", exact: true }).click();
    await expect(
      page.getByText("delivery.created", { exact: true }),
    ).toBeVisible();
    await page.goto(`/destinations/${destinationId}`);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(
      page.getByLabel("Destination name", { exact: true }),
    ).toHaveValue(name);
    await page
      .getByLabel("Description", { exact: true })
      .fill("Verified through the real browser workflow");
    await page
      .getByRole("button", { name: "Save destination", exact: true })
      .click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await page.getByRole("tab", { name: "Overview", exact: true }).click();
    await page.screenshot({
      path: resolve(root, "artifacts/destination-overview.png"),
      fullPage: true,
    });
    sql(
      "cdc-source-postgres",
      "commerce",
      `DELETE FROM orders WHERE id=${orderId}; DELETE FROM customers WHERE id=${customerId};`,
    );
    await expect
      .poll(() =>
        sql(
          "destination-postgres",
          "analytics",
          `SELECT count(*) FROM analytics.${table} WHERE id=${customerId};`,
        ),
      )
      .toBe("0");
    await page.goto(`/pipelines/${pipeline.id}`);
    await expect(
      page.getByRole("link", { name: "Primary delivery", exact: true }).first(),
    ).toBeVisible();
    await page.screenshot({
      path: resolve(root, "artifacts/pipeline-delivery-flow.png"),
      fullPage: true,
    });
    expect(errors).toEqual([]);
  } finally {
    if (orderId)
      sql(
        "cdc-source-postgres",
        "commerce",
        `DELETE FROM orders WHERE id=${orderId};`,
      );
    if (customerId)
      sql(
        "cdc-source-postgres",
        "commerce",
        `DELETE FROM customers WHERE id=${customerId};`,
      );
    if (destinationId) {
      const response = await request.get(
        `/api/v1/destinations/${destinationId}`,
      );
      if (response.ok()) {
        const detail = await response.json();
        for (const link of detail.deliveries)
          await request.delete(
            `/api/v1/destinations/${destinationId}/deliveries/${link.id}`,
          );
        await request.delete(`/api/v1/destinations/${destinationId}`);
      }
    }
    sql(
      "destination-postgres",
      "analytics",
      `DROP TABLE IF EXISTS analytics.${table},analytics.${table}_v2,analytics.${orders};`,
    );
  }
});

test("delivery list, runtime and wizard remain usable on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/deliveries");
  await page.getByLabel("Delivery status").selectOption("FAILED");
  await expect(
    page.getByRole("link", { name: /^Primary delivery/ }),
  ).toHaveCount(0);
  await page.getByLabel("Delivery status").selectOption("RUNNING");
  await page
    .getByRole("link", { name: /^Primary delivery/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Primary delivery", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Tasks", exact: true }).click();
  await expect(
    page.getByText("running", { exact: true }).first(),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  mkdirSync(resolve(root, "artifacts"), { recursive: true });
  await page.screenshot({
    path: resolve(root, "artifacts/destination-mobile.png"),
    fullPage: true,
  });
  await page.goto("/deliveries/new?type=DATABASE");
  await expect(page.getByRole("button", { name: /MySQL/ })).toBeEnabled();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByLabel("Host", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("real authentication failure is visible and can be acknowledged", async ({
  page,
  request,
}) => {
  const response = await request.post("/api/v1/destinations", {
    data: {
      name: `Rejected destination ${Date.now()}`,
      environment: "DEV",
      host: "destination-postgres",
      database_name: "analytics",
      username: "delivery_user",
      password: "intentionally-invalid-test-password",
    },
  });
  expect(response.ok()).toBeTruthy();
  const target = await response.json();
  try {
    const failed = await request.post(`/api/v1/destinations/${target.id}/test`);
    expect(failed.status()).toBe(422);
    expect((await failed.json()).error.code).toBe("DESTINATION_AUTH_FAILED");
    await page.goto("/operations/errors");
    const incident = page
      .getByRole("row")
      .filter({ has: page.locator(`a[href="/destinations/${target.id}"]`) });
    await expect(incident).toContainText(
      "Destination rejected the supplied credentials",
    );
    await incident
      .getByRole("button", { name: "Acknowledge", exact: true })
      .click();
    await page
      .getByLabel("Filter incidents", { exact: true })
      .selectOption("ACKNOWLEDGED");
    await expect(incident).toContainText("acknowledged");
    await incident
      .getByRole("button", { name: "Resolve", exact: true })
      .click();
    await page
      .getByLabel("Filter incidents", { exact: true })
      .selectOption("RESOLVED");
    await expect(incident).toContainText("resolved");
    expect(await page.locator("body").innerText()).not.toContain(
      "intentionally-invalid-test-password",
    );
  } finally {
    await request.delete(`/api/v1/destinations/${target.id}`);
  }
});

test("real sink constraint failure becomes degraded and recovers after repair", async ({
  page,
  request,
}) => {
  test.setTimeout(240000);
  const token = Date.now().toString();
  const table = `browser_failure_${token}`;
  const pipelines = await (await request.get("/api/v1/pipelines")).json();
  const pipeline = pipelines.find(
    (value: { name: string }) => value.name === "Commerce capture",
  );
  const customerTopic = `${pipeline.topic_prefix}.public.customers`;
  sql(
    "destination-postgres",
    "analytics",
    `CREATE TABLE analytics.${table}(id bigint PRIMARY KEY,name text NOT NULL CONSTRAINT browser_name_guard CHECK(name NOT LIKE 'Blocked %'),email text NOT NULL,updated_at timestamptz NOT NULL,age integer,phone varchar(20)); ALTER TABLE analytics.${table} OWNER TO delivery_user;`,
  );
  const target = await (
    await request.post("/api/v1/destinations", {
      data: {
        name: `Failure destination ${token}`,
        environment: "DEV",
        host: "destination-postgres",
        database_name: "analytics",
        username: "delivery_user",
        password: env.DESTINATION_PASSWORD,
      },
    })
  ).json();
  let customerId = 0;
  try {
    const deployment = await request.post(
      `/api/v1/destinations/${target.id}/deploy`,
      {
        data: {
          pipeline_id: pipeline.id,
          mappings: [
            {
              topic: customerTopic,
              schema_name: "analytics",
              table_name: table,
            },
          ],
          auto_create: true,
          auto_evolve: true,
          max_retries: 0,
        },
      },
    );
    const deploymentPayload = await deployment.json();
    expect(deployment.ok(), JSON.stringify(deploymentPayload)).toBeTruthy();
    const delivery = deploymentPayload;
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${target.id}/status`)
            ).json()
          ).actual_state,
      )
      .toBe("RUNNING");
    customerId = Number(
      sql(
        "cdc-source-postgres",
        "commerce",
        `INSERT INTO customers(name,email) VALUES ('Blocked ${token}','blocked-${token}@example.test') RETURNING id;`,
      ),
    );
    // JDBC 3.3 stores the first processing exception and raises it on the next put.
    // Wait for this run's CREATE in Kafka, then send another change to make Connect
    // publish the task failure without treating RUNNING as a successful write.
    await expect
      .poll(async () => {
        const response = await request.get(
          `/api/v1/events?cluster_id=${pipeline.kafka_cluster_id}&topic=${encodeURIComponent(customerTopic)}&operation=CREATE&limit=200`,
        );
        const sample = await response.json();
        return sample.events.some(
          (event: { after?: { name?: string } }) =>
            event.after?.name === `Blocked ${token}`,
        );
      })
      .toBe(true);
    sql(
      "cdc-source-postgres",
      "commerce",
      `UPDATE customers SET name='Blocked ${token} again',updated_at=now() WHERE id=${customerId};`,
    );
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${target.id}/status`)
            ).json()
          ).actual_state,
        { timeout: 60000 },
      )
      .toBe("DEGRADED");
    await page.goto(`/deliveries/${delivery.id}`);
    await expect(
      page.getByText("degraded", { exact: true }).first(),
    ).toBeVisible();
    await page.getByRole("tab", { name: "Tasks", exact: true }).click();
    await expect(
      page.getByText("failed", { exact: true }).first(),
    ).toBeVisible();
    mkdirSync(resolve(root, "artifacts"), { recursive: true });
    await page.screenshot({
      path: resolve(root, "artifacts/destination-failure.png"),
      fullPage: true,
    });
    const incidents = await (
      await request.get("/api/v1/operations/errors")
    ).json();
    expect(
      incidents.some(
        (incident: {
          destination_id: string;
          connector_id: string;
          pipeline_id: string;
          category: string;
          status: string;
        }) =>
          incident.destination_id === target.id &&
          incident.connector_id === delivery.connector_id &&
          incident.pipeline_id === pipeline.id &&
          incident.category === "SINK_CONNECTOR" &&
          incident.status === "OPEN",
      ),
    ).toBeTruthy();
    sql(
      "destination-postgres",
      "analytics",
      `ALTER TABLE analytics.${table} DROP CONSTRAINT browser_name_guard;`,
    );
    await page.getByRole("button", { name: "Restart", exact: true }).click();
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`/api/v1/destinations/${target.id}/status`)
            ).json()
          ).actual_state,
      )
      .toBe("RUNNING");
    await expect
      .poll(() =>
        sql(
          "destination-postgres",
          "analytics",
          `SELECT count(*) FROM analytics.${table} WHERE id=${customerId};`,
        ),
      )
      .toBe("1");
    expect(
      (await (await request.get("/api/v1/operations/errors")).json())
        .filter(
          (incident: { destination_id: string; category: string }) =>
            incident.destination_id === target.id &&
            incident.category === "SINK_CONNECTOR",
        )
        .every(
          (incident: { status: string }) => incident.status === "RESOLVED",
        ),
    ).toBeTruthy();
  } finally {
    if (customerId)
      sql(
        "cdc-source-postgres",
        "commerce",
        `DELETE FROM customers WHERE id=${customerId};`,
      );
    const detail = await (
      await request.get(`/api/v1/destinations/${target.id}`)
    ).json();
    for (const link of detail.deliveries)
      await request.delete(
        `/api/v1/destinations/${target.id}/deliveries/${link.id}`,
      );
    await request.delete(`/api/v1/destinations/${target.id}`);
    sql(
      "destination-postgres",
      "analytics",
      `DROP TABLE IF EXISTS analytics.${table};`,
    );
  }
});
