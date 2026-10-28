"use client";

import { Field } from "@/components/common";
import type {
  ConnectionCategory,
  ConnectionProvider,
} from "@cluecdc/contracts";
import { Input } from "@cluecdc/ui";
import { HardDrive, Server } from "lucide-react";

export const categoryCopy: Record<
  ConnectionCategory,
  { title: string; description: string; icon: typeof Server }
> = {
  DATABASE: {
    title: "Database",
    description: "CDC sources and database delivery targets.",
    icon: Server,
  },
  OBJECT_STORAGE: {
    title: "Object storage",
    description: "JSONL CDC archives delivered by Kafka Connect.",
    icon: HardDrive,
  },
};

export const providerName = (provider: string) =>
  ({
    POSTGRESQL: "PostgreSQL",
    MYSQL: "MySQL",
    AWS_S3: "AWS S3",
    MINIO: "MinIO",
    AZURE_SYNAPSE: "Azure Synapse Analytics",
  })[provider] || provider;

export type FormState = {
  name: string;
  description: string;
  config: Record<string, unknown>;
  credentials: Record<string, string>;
};

export const initialState = (provider: ConnectionProvider): FormState => {
  if (provider === "AZURE_SYNAPSE") {
    return {
      name: "",
      description: "",
      config: {
        workspace_name: "",
        sql_endpoint: "",
        port: 1433,
        database_name: "",
        target_schema: "dbo",
        authentication: "SQL_PASSWORD",
        username: "",
        tenant_id: "",
        client_id: "",
        ssl_enabled: true,
        storage_account: "",
        storage_type: "ADLS_GEN2",
        container: "",
        staging_prefix: "cluecdc",
        storage_authentication: "ACCOUNT_KEY",
        staging_retention_days: 7,
        batch_size: 10000,
        batch_flush_interval_ms: 30000,
        maximum_concurrency: 2,
        max_retries: 5,
        retry_backoff_ms: 1000,
        statement_timeout_seconds: 900,
        distribution_strategy: "ROUND_ROBIN",
      },
      credentials: {
        password: "",
        client_secret: "",
        storage_account_key: "",
        storage_sas_token: "",
      },
    };
  }
  if (["POSTGRESQL", "MYSQL"].includes(provider)) {
    const ports: Record<string, number> = {
      POSTGRESQL: 5432,
      MYSQL: 3306,
    };
    return {
      name: "",
      description: "",
      config: {
        host: "localhost",
        port: ports[provider],
        database_name: "",
        username: "",
        ssl_enabled: false,
        environment: "DEV",
      },
      credentials: { password: "" },
    };
  }
  return {
    name: "",
    description: "",
    config: {
      bucket: "",
      region: "us-east-1",
      endpoint: provider === "MINIO" ? "http://minio:9000" : "",
      prefix: "",
      use_ssl: provider !== "MINIO",
      path_style_access: provider === "MINIO",
      tls_verify: true,
      connection_timeout_seconds: 10,
    },
    credentials: { access_key: "", secret_key: "", session_token: "" },
  };
};

export const providerCategory = (
  provider: ConnectionProvider,
): ConnectionCategory =>
  provider === "AWS_S3" || provider === "MINIO" ? "OBJECT_STORAGE" : "DATABASE";

export function ProviderFields({
  provider,
  form,
  config,
  secret,
  managedIdentityAvailable = false,
}: {
  provider: ConnectionProvider;
  form: FormState;
  config: (key: string, value: string | number | boolean) => void;
  secret: (key: string, value: string) => void;
  managedIdentityAvailable?: boolean;
}) {
  if (provider === "AZURE_SYNAPSE") {
    const authentication = String(form.config.authentication);
    const storageAuthentication = String(form.config.storage_authentication);
    return (
      <>
        <h3 className="full-width">Dedicated SQL Pool</h3>
        <Field label="Workspace name">
          <Input
            value={String(form.config.workspace_name)}
            onChange={(e) => config("workspace_name", e.target.value)}
          />
        </Field>
        <Field label="Dedicated SQL endpoint">
          <Input
            value={String(form.config.sql_endpoint)}
            placeholder="workspace.sql.azuresynapse.net"
            onChange={(e) => config("sql_endpoint", e.target.value)}
          />
        </Field>
        <Field label="Port">
          <Input
            type="number"
            value={Number(form.config.port)}
            onChange={(e) => config("port", Number(e.target.value))}
          />
        </Field>
        <Field label="SQL pool database">
          <Input
            value={String(form.config.database_name)}
            onChange={(e) => config("database_name", e.target.value)}
          />
        </Field>
        <Field label="Target schema">
          <Input
            value={String(form.config.target_schema)}
            onChange={(e) => config("target_schema", e.target.value)}
          />
        </Field>
        <Field label="Authentication">
          <select
            value={authentication}
            onChange={(e) => config("authentication", e.target.value)}
          >
            <option value="SQL_PASSWORD">SQL username and password</option>
            <option value="SERVICE_PRINCIPAL">
              Microsoft Entra service principal
            </option>
            {managedIdentityAvailable && (
              <option value="MANAGED_IDENTITY">Managed identity</option>
            )}
          </select>
        </Field>
        {authentication === "SQL_PASSWORD" ? (
          <>
            <Field label="SQL username">
              <Input
                value={String(form.config.username)}
                onChange={(e) => config("username", e.target.value)}
              />
            </Field>
            <Field label="SQL password">
              <Input
                type="password"
                autoComplete="new-password"
                value={form.credentials.password || ""}
                onChange={(e) => secret("password", e.target.value)}
              />
            </Field>
          </>
        ) : authentication === "SERVICE_PRINCIPAL" ? (
          <>
            <Field label="Tenant ID">
              <Input
                value={String(form.config.tenant_id)}
                onChange={(e) => config("tenant_id", e.target.value)}
              />
            </Field>
            <Field label="Client ID">
              <Input
                value={String(form.config.client_id)}
                onChange={(e) => config("client_id", e.target.value)}
              />
            </Field>
            <Field label="Client secret">
              <Input
                type="password"
                autoComplete="new-password"
                value={form.credentials.client_secret || ""}
                onChange={(e) => secret("client_secret", e.target.value)}
              />
            </Field>
          </>
        ) : (
          <Field label="User-assigned identity client ID (optional)">
            <Input
              value={String(form.config.managed_identity_client_id || "")}
              onChange={(e) =>
                config("managed_identity_client_id", e.target.value)
              }
            />
          </Field>
        )}
        <p className="muted full-width">
          TLS certificate validation is always enabled. Serverless SQL Pool is
          not supported as a mutable CDC destination.
        </p>
        <h3 className="full-width">Azure Storage staging</h3>
        <Field label="Storage account">
          <Input
            value={String(form.config.storage_account)}
            onChange={(e) => config("storage_account", e.target.value)}
          />
        </Field>
        <Field label="Storage type">
          <select
            value={String(form.config.storage_type)}
            onChange={(e) => config("storage_type", e.target.value)}
          >
            <option value="ADLS_GEN2">ADLS Gen2</option>
            <option value="BLOB">Blob Storage</option>
          </select>
        </Field>
        <Field label="Container">
          <Input
            value={String(form.config.container)}
            onChange={(e) => config("container", e.target.value)}
          />
        </Field>
        <Field label="Staging prefix">
          <Input
            value={String(form.config.staging_prefix)}
            onChange={(e) => config("staging_prefix", e.target.value)}
          />
        </Field>
        <Field label="Storage authentication">
          <select
            value={storageAuthentication}
            onChange={(e) => config("storage_authentication", e.target.value)}
          >
            <option value="ACCOUNT_KEY">Storage account key</option>
            <option value="SAS_TOKEN">SAS token</option>
            {authentication === "SERVICE_PRINCIPAL" && (
              <option value="SERVICE_PRINCIPAL">Service principal</option>
            )}
            {authentication === "MANAGED_IDENTITY" && (
              <option value="MANAGED_IDENTITY">Managed identity</option>
            )}
          </select>
        </Field>
        {storageAuthentication === "ACCOUNT_KEY" && (
          <Field label="Storage account key">
            <Input
              type="password"
              autoComplete="off"
              value={form.credentials.storage_account_key || ""}
              onChange={(e) => secret("storage_account_key", e.target.value)}
            />
          </Field>
        )}
        {storageAuthentication === "SAS_TOKEN" && (
          <Field label="Storage SAS token">
            <Input
              type="password"
              autoComplete="off"
              value={form.credentials.storage_sas_token || ""}
              onChange={(e) => secret("storage_sas_token", e.target.value)}
            />
          </Field>
        )}
        <h3 className="full-width">Batch settings</h3>
        <Field label="Batch size">
          <Input
            type="number"
            value={Number(form.config.batch_size)}
            onChange={(e) => config("batch_size", Number(e.target.value))}
          />
        </Field>
        <Field label="Flush interval (ms)">
          <Input
            type="number"
            value={Number(form.config.batch_flush_interval_ms)}
            onChange={(e) =>
              config("batch_flush_interval_ms", Number(e.target.value))
            }
          />
        </Field>
        <Field label="Maximum concurrency">
          <Input
            type="number"
            value={Number(form.config.maximum_concurrency)}
            onChange={(e) =>
              config("maximum_concurrency", Number(e.target.value))
            }
          />
        </Field>
        <Field label="Distribution strategy">
          <select
            value={String(form.config.distribution_strategy)}
            onChange={(e) => config("distribution_strategy", e.target.value)}
          >
            <option value="ROUND_ROBIN">ROUND_ROBIN</option>
            <option value="HASH">HASH</option>
            <option value="REPLICATE">REPLICATE</option>
          </select>
        </Field>
      </>
    );
  }
  if (["POSTGRESQL", "MYSQL"].includes(provider))
    return (
      <>
        <Field label="Host">
          <Input
            value={String(form.config.host)}
            onChange={(e) => config("host", e.target.value)}
          />
        </Field>
        <Field label="Port">
          <Input
            type="number"
            value={Number(form.config.port)}
            onChange={(e) => config("port", Number(e.target.value))}
          />
        </Field>
        <Field label="Database">
          <Input
            value={String(form.config.database_name)}
            onChange={(e) => config("database_name", e.target.value)}
          />
        </Field>
        <Field label="Username">
          <Input
            value={String(form.config.username)}
            onChange={(e) => config("username", e.target.value)}
          />
        </Field>
        <Field label="Password">
          <Input
            type="password"
            autoComplete="new-password"
            value={form.credentials.password}
            onChange={(e) => secret("password", e.target.value)}
          />
        </Field>
        <Field label="Environment">
          <select
            value={String(form.config.environment)}
            onChange={(e) => config("environment", e.target.value)}
          >
            <option value="DEV">Development</option>
            <option value="STAGING">Staging</option>
            <option value="PROD">Production</option>
          </select>
        </Field>
        <label className="check-row">
          <input
            type="checkbox"
            checked={Boolean(form.config.ssl_enabled)}
            onChange={(e) => config("ssl_enabled", e.target.checked)}
          />{" "}
          TLS enabled
        </label>
      </>
    );
  return (
    <>
      <Field label="Bucket">
        <Input
          value={String(form.config.bucket)}
          onChange={(e) => config("bucket", e.target.value)}
        />
      </Field>
      <Field label="Region / signing region">
        <Input
          value={String(form.config.region)}
          onChange={(e) => config("region", e.target.value)}
        />
      </Field>
      <Field
        label={
          provider === "MINIO"
            ? "MinIO endpoint"
            : "Endpoint override (optional)"
        }
      >
        <Input
          value={String(form.config.endpoint)}
          onChange={(e) => config("endpoint", e.target.value)}
          placeholder="http://minio:9000"
        />
      </Field>
      <Field label="Object prefix (optional)">
        <Input
          value={String(form.config.prefix)}
          onChange={(e) => config("prefix", e.target.value)}
        />
      </Field>
      <Field label="Access key">
        <Input
          type="password"
          autoComplete="off"
          value={form.credentials.access_key || ""}
          onChange={(e) => secret("access_key", e.target.value)}
        />
      </Field>
      <Field label="Secret key">
        <Input
          type="password"
          autoComplete="off"
          value={form.credentials.secret_key || ""}
          onChange={(e) => secret("secret_key", e.target.value)}
        />
      </Field>
      {provider === "AWS_S3" && (
        <Field label="Session token (optional)">
          <Input
            type="password"
            autoComplete="off"
            value={form.credentials.session_token || ""}
            onChange={(e) => secret("session_token", e.target.value)}
          />
        </Field>
      )}
      <Field label="Connection timeout (seconds)">
        <Input
          type="number"
          min={1}
          max={120}
          value={Number(form.config.connection_timeout_seconds)}
          onChange={(e) =>
            config("connection_timeout_seconds", Number(e.target.value))
          }
        />
      </Field>
      <label className="check-row">
        <input
          type="checkbox"
          checked={Boolean(form.config.use_ssl)}
          onChange={(e) => config("use_ssl", e.target.checked)}
        />{" "}
        TLS enabled
      </label>
      <label className="check-row">
        <input
          type="checkbox"
          checked={Boolean(form.config.path_style_access)}
          onChange={(e) => config("path_style_access", e.target.checked)}
        />{" "}
        Path-style access
      </label>
    </>
  );
}
