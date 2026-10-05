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
  })[provider] || provider;

export type FormState = {
  name: string;
  description: string;
  config: Record<string, string | number | boolean>;
  credentials: Record<string, string>;
};

export const initialState = (provider: ConnectionProvider): FormState => {
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
}: {
  provider: ConnectionProvider;
  form: FormState;
  config: (key: string, value: string | number | boolean) => void;
  secret: (key: string, value: string) => void;
}) {
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
