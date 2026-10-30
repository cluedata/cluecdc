import { test, expect, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ConnectionProvider } from "@cluecdc/contracts";
import { signIn } from "./auth";

const fileEnv = Object.fromEntries(
  [".env.test.example", ".env"].flatMap((file) =>
    readFileSync(resolve(process.cwd(), "../..", file), "utf8")
      .split(/\r?\n/)
      .filter((line) => line.includes("=") && !line.startsWith("#"))
      .map((line) => [
        line.slice(0, line.indexOf("=")),
        line.slice(line.indexOf("=") + 1),
      ]),
  ),
);
const env = (key: string) => process.env[key] ?? fileEnv[key];
const providers: ConnectionProvider[] = [
  "POSTGRESQL",
  "MYSQL",
  "AWS_S3",
  "MINIO",
];
const providerName = {
  POSTGRESQL: "PostgreSQL",
  MYSQL: "MySQL",
  AWS_S3: "AWS S3",
  MINIO: "MinIO",
  AZURE_SYNAPSE: "Azure Synapse Analytics",
  CLICKHOUSE: "ClickHouse",
  BIGQUERY: "Google BigQuery",
  REDSHIFT: "Amazon Redshift",
};

function destinationPayload(provider: ConnectionProvider) {
  const database = provider === "POSTGRESQL" || provider === "MYSQL";
  return {
    name: `Connection test ${provider} ${Date.now()}`,
    category: database ? "DATABASE" : "OBJECT_STORAGE",
    provider,
    capabilities: ["DESTINATION"],
    config: database
      ? {
          host: provider === "MYSQL" ? "mysql-demo" : "postgres-demo",
          port: provider === "MYSQL" ? 3306 : 5432,
          database_name: "cluecdc_destination",
          username: "destination_user",
          environment: "DEV",
        }
      : {
          bucket: "cluecdc-cdc",
          region: "us-east-1",
          endpoint: "http://minio:9000",
          use_ssl: false,
          path_style_access: true,
        },
    credentials: database
      ? {
          password: env(
            provider === "MYSQL"
              ? "MYSQL_DEMO_DESTINATION_PASSWORD"
              : "POSTGRES_DEMO_DESTINATION_PASSWORD",
          ),
        }
      : {
          access_key: env("MINIO_ACCESS_KEY"),
          secret_key: env("MINIO_SECRET_KEY"),
        },
  };
}

test.beforeEach(async ({ page, request }) => {
  await signIn(page, request);
});

async function fillConnection(page: Page, provider: ConnectionProvider) {
  const data = destinationPayload(provider);
  await page.getByLabel("Connection name", { exact: true }).fill(data.name);
  if (provider === "POSTGRESQL" || provider === "MYSQL") {
    await page.getByLabel("Host", { exact: true }).fill(data.config.host!);
    await page
      .getByLabel("Port", { exact: true })
      .fill(String(data.config.port));
    await page
      .getByLabel("Database", { exact: true })
      .fill(data.config.database_name!);
    await page
      .getByLabel("Username", { exact: true })
      .fill(data.config.username!);
    await page
      .getByLabel("Password", { exact: true })
      .fill(data.credentials.password!);
  } else {
    await page.getByLabel("Bucket", { exact: true }).fill("cluecdc-cdc");
    await page
      .getByLabel(
        provider === "MINIO"
          ? "MinIO endpoint"
          : "Endpoint override (optional)",
        { exact: true },
      )
      .fill("http://minio:9000");
    await page.getByLabel("TLS enabled", { exact: true }).uncheck();
    await page.getByLabel("Path-style access", { exact: true }).check();
    await page
      .getByLabel("Access key", { exact: true })
      .fill(env("MINIO_ACCESS_KEY"));
    await page
      .getByLabel("Secret key", { exact: true })
      .fill(env("MINIO_SECRET_KEY"));
  }
}

for (const width of [1440, 390]) {
  for (const provider of providers) {
    test(`${provider} creation tests current inputs without saving at ${width}px`, async ({
      page,
      request,
    }, testInfo) => {
      const original = await (await request.get("/api/v1/connections")).json();
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(
        provider === "POSTGRESQL" || provider === "MINIO"
          ? "/connections/new"
          : "/destinations/new",
      );
      await page.getByText(providerName[provider], { exact: true }).click();
      await fillConnection(page, provider);
      const button = page.getByRole("button", {
        name: "Test connection",
        exact: true,
      });
      await expect(button).toBeEnabled();
      await button.click();
      await expect(
        page.getByText("Connection test passed", { exact: true }),
      ).toBeVisible();
      if (
        process.env.CLUECDC_UI_SCREENSHOTS === "true" &&
        ["POSTGRESQL", "MINIO"].includes(provider)
      ) {
        await page.screenshot({
          path: testInfo.outputPath(`connection-${provider}-${width}.png`),
          fullPage: true,
        });
      }
      await expect
        .poll(
          async () =>
            (await (await request.get("/api/v1/connections")).json()).length,
        )
        .toBe(original.length);
      await page
        .getByLabel("Connection name", { exact: true })
        .fill("Changed draft");
      await expect(
        page.getByText("Connection test passed", { exact: true }),
      ).toHaveCount(0);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      expect(errors).toEqual([]);
    });
  }
}

for (const provider of providers) {
  test(`${provider} edit tests retained credentials without saving changes`, async ({
    page,
    request,
  }) => {
    const created = await request.post("/api/v1/connections", {
      data: destinationPayload(provider),
    });
    expect(created.status()).toBe(201);
    const connection = await created.json();
    const original = await (
      await request.get(`/api/v1/connections/${connection.id}`)
    ).json();
    try {
      await page.goto(`/connections/${connection.id}/edit`);
      await page
        .getByLabel("Connection name", { exact: true })
        .fill("Unsaved changed connection");
      await page
        .getByRole("button", { name: "Test connection", exact: true })
        .click();
      await expect(
        page.getByText("Connection test passed", { exact: true }),
      ).toBeVisible();
      expect(
        await (
          await request.get(`/api/v1/connections/${connection.id}`)
        ).json(),
      ).toEqual(original);
    } finally {
      expect(
        (await request.delete(`/api/v1/connections/${connection.id}`)).status(),
      ).toBe(200);
    }
  });
}

for (const type of ["postgresql", "mysql"]) {
  test(`${type} source edit tests retained credentials and legacy environment values`, async ({
    page,
    request,
  }) => {
    const created = await request.post("/api/v1/connections", {
      data: {
        name: `Source edit test ${Date.now()}`,
        category: "DATABASE",
        provider: type.toUpperCase(),
        capabilities: ["SOURCE"],
        config: {
          host: type === "mysql" ? "mysql-demo" : "postgres-demo",
          port: type === "mysql" ? 3306 : 5432,
          database_name: "cluecdc_source",
          username: "source_user",
          environment: "DEV",
        },
        credentials: {
          password: env(
            type === "mysql"
              ? "MYSQL_DEMO_SOURCE_PASSWORD"
              : "POSTGRES_DEMO_SOURCE_PASSWORD",
          ),
        },
      },
    });
    expect(created.status()).toBe(201);
    const source = await created.json();
    const original = await (
      await request.get(`/api/v1/connections/${source.id}`)
    ).json();
    try {
      await page.goto(`/sources/${source.id}`);
      await page
        .getByRole("tab", { name: "Configuration", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Edit source", exact: true })
        .click();
      const dialog = page.getByRole("dialog", {
        name: "Edit source",
        exact: true,
      });
      await dialog
        .getByRole("button", { name: "Test connection", exact: true })
        .click();
      await expect(
        dialog.getByText("Connection test passed", { exact: true }),
      ).toBeVisible();
      expect(
        await (await request.get(`/api/v1/connections/${source.id}`)).json(),
      ).toEqual(original);
    } finally {
      await request.delete(`/api/v1/connections/${source.id}`);
    }
  });
  test(`${type} source form tests before registration and displays authentication errors`, async ({
    page,
    request,
  }) => {
    const original = await (await request.get("/api/v1/connections")).json();
    await page.goto("/sources/new");
    await page.getByLabel("Database type", { exact: true }).selectOption(type);
    await page
      .getByLabel("Host", { exact: true })
      .fill(type === "mysql" ? "mysql-demo" : "postgres-demo");
    await page
      .getByLabel("Password", { exact: true })
      .fill("incorrect-password");
    await page
      .getByRole("button", { name: "Test connection", exact: true })
      .click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(
      page.getByText("Connection test passed", { exact: true }),
    ).toHaveCount(0);
    await page
      .getByLabel("Password", { exact: true })
      .fill(
        env(
          type === "mysql"
            ? "MYSQL_DEMO_SOURCE_PASSWORD"
            : "POSTGRES_DEMO_SOURCE_PASSWORD",
        ),
      );
    await page
      .getByRole("button", { name: "Test connection", exact: true })
      .click();
    await expect(
      page.getByText("Connection test passed", { exact: true }),
    ).toBeVisible();
    expect(
      (await (await request.get("/api/v1/connections")).json()).length,
    ).toBe(original.length);
  });
}

test("an in-use connection is protected by the API and deletion dialog", async ({
  page,
  request,
}) => {
  const evidencePath = resolve(
    process.cwd(),
    "../..",
    "artifacts",
    "cdc-effective-key.json",
  );
  test.skip(
    !existsSync(evidencePath),
    "Run scripts/cdc-effective-key.py to provision the live CDC fixture",
  );
  const evidence = JSON.parse(readFileSync(evidencePath, "utf8")) as {
    pipelines: { postgresql: string };
  };
  const pipelineResponse = await request.get(
    `/api/v1/pipelines/${evidence.pipelines.postgresql}`,
  );
  expect(pipelineResponse.status()).toBe(200);
  const pipeline = (await pipelineResponse.json()) as {
    id: string;
    name: string;
    source: { id: string; name: string };
    actual_state: string;
  };

  const blocked = await request.delete(
    `/api/v1/connections/${pipeline.source.id}`,
  );
  expect(blocked.status()).toBe(409);
  const error = (await blocked.json()).error;
  expect(error.code).toBe("CONNECTION_IN_USE");
  expect(error.details.used_by).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        type: "pipeline",
        id: pipeline.id,
        name: pipeline.name,
      }),
    ]),
  );

  await page.goto("/connections");
  await page.getByLabel("Search connections").fill(pipeline.source.name);
  const row = page.getByRole("row").filter({ hasText: pipeline.source.name });
  await expect(row).toHaveCount(1);
  await row
    .getByRole("button", { name: `Delete ${pipeline.source.name}` })
    .click();
  const dialog = page.getByRole("dialog", { name: "Delete connection?" });
  await expect(dialog.getByText("Cannot delete connection")).toBeVisible();
  await expect(dialog.getByText(`Pipeline: ${pipeline.name}`)).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Delete connection" }),
  ).toBeDisabled();

  const after = await request.get(`/api/v1/pipelines/${pipeline.id}`);
  expect(after.status()).toBe(200);
  expect((await after.json()).actual_state).toBe(pipeline.actual_state);
});

test("effective unique keys agree between the API and pipeline wizard", async ({
  page,
  request,
}) => {
  const evidencePath = resolve(
    process.cwd(),
    "../..",
    "artifacts",
    "cdc-effective-key.json",
  );
  test.skip(
    !existsSync(evidencePath),
    "Run scripts/cdc-effective-key.py to provision the live CDC fixture",
  );
  const evidence = JSON.parse(readFileSync(evidencePath, "utf8")) as {
    pipelines: { postgresql: string };
  };
  const response = await request.get(
    `/api/v1/pipelines/${evidence.pipelines.postgresql}`,
  );
  expect(response.status()).toBe(200);
  const pipeline = (await response.json()) as {
    source: { id: string };
    kafka_cluster: { id: string };
    connect_cluster: { id: string };
    tables: Array<{
      schema_name: string;
      table_name: string;
      effective_key_mode: string;
      effective_key_columns: string[];
    }>;
  };

  await page.goto(`/pipelines/new?source=${pipeline.source.id}`);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page
    .getByLabel("Kafka cluster", { exact: true })
    .selectOption(pipeline.kafka_cluster.id);
  await page
    .getByLabel("Kafka Connect cluster", { exact: true })
    .selectOption(pipeline.connect_cluster.id);
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  for (const table of pipeline.tables) {
    expect(table.effective_key_mode).toBe("UNIQUE_INDEX");
    const row = page
      .getByRole("row")
      .filter({ hasText: `${table.schema_name}.${table.table_name}` });
    await expect(row).toHaveCount(1);
    await expect(
      row.getByText(table.effective_key_columns.join(", "), { exact: true }),
    ).toBeVisible();
    await expect(row.getByText("UNIQUE INDEX", { exact: true })).toBeVisible();
    await expect(row.getByText("CURRENT STATE", { exact: true })).toBeVisible();
  }
});

for (const provider of ["POSTGRESQL", "MYSQL"] as const) {
  test(`${provider} legacy destination edit dialog tests draft values with its saved password`, async ({
    page,
    request,
  }) => {
    const created = await request.post("/api/v1/connections", {
      data: destinationPayload(provider),
    });
    expect(created.status()).toBe(201);
    const connection = await created.json();
    try {
      await page.goto(`/destinations/${connection.id}?edit=true`);
      const dialog = page.getByRole("dialog", {
        name: "Edit destination",
        exact: true,
      });
      await dialog
        .getByRole("button", { name: "Test connection", exact: true })
        .click();
      await expect(
        dialog.getByText("Connection test passed", { exact: true }),
      ).toBeVisible();
    } finally {
      await request.delete(`/api/v1/connections/${connection.id}`);
    }
  });
}

test("Kafka and Kafka Connect create/edit forms test before saving", async ({
  page,
  request,
}) => {
  await page.goto("/kafka/clusters");
  await page
    .getByRole("button", { name: "Register cluster", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Test connection", exact: true })
    .click();
  await expect(
    dialog.getByText("Kafka connection succeeded", { exact: true }),
  ).toBeVisible();
  const created = await request.post("/api/v1/kafka/clusters", {
    data: {
      name: `Test dependency ${Date.now()}`,
      bootstrap_servers: "kafka:29092",
    },
  });
  expect(created.status()).toBe(201);
  const kafka = await created.json();
  let connectId: string | undefined;
  try {
    await page.goto("/kafka/clusters");
    await page
      .getByRole("button", { name: `Edit ${kafka.name}`, exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByRole("button", { name: "Test connection", exact: true })
      .click();
    await expect(
      dialog.getByText("Kafka connection succeeded", { exact: true }),
    ).toBeVisible();
    await page.goto("/kafka/connect-clusters");
    await page
      .getByRole("button", { name: "Register cluster", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("Kafka cluster", { exact: true })
      .selectOption(kafka.id);
    await dialog
      .getByRole("button", { name: "Test connection", exact: true })
      .click();
    await expect(
      dialog.getByText("Kafka Connect connection succeeded", { exact: true }),
    ).toBeVisible();
    const createdConnect = await request.post("/api/v1/connect/clusters", {
      data: {
        name: `Connect test ${Date.now()}`,
        base_url: "http://kafka-connect:8083",
        kafka_cluster_id: kafka.id,
      },
    });
    expect(createdConnect.status()).toBe(201);
    const connect = await createdConnect.json();
    connectId = connect.id;
    await page.goto("/kafka/connect-clusters");
    await page
      .getByRole("button", { name: `Edit ${connect.name}`, exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByRole("button", { name: "Test connection", exact: true })
      .click();
    await expect(
      dialog.getByText("Kafka Connect connection succeeded", { exact: true }),
    ).toBeVisible();
  } finally {
    if (connectId)
      await request.delete(`/api/v1/connect/clusters/${connectId}`);
    await request.delete(`/api/v1/kafka/clusters/${kafka.id}`);
  }
});
