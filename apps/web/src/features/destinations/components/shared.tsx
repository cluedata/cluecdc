"use client";

import { Field } from "@/components/common";
import { api } from "@/lib/api";
import { type DestinationForm } from "@/lib/validation";
import type {
  DatabaseProviderMetadata,
  DeliveryConfiguration,
  Destination,
} from "@cluecdc/contracts";
import { Input } from "@cluecdc/ui";
import { useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";

export const initialConnection: DestinationForm = {
  name: "",
  description: "",
  type: "postgresql",
  environment: "DEV",
  host: "",
  port: 5432,
  database_name: "",
  username: "",
  password: "",
  ssl_enabled: false,
  provider_options: { connection_timeout_seconds: 10 },
};

export const initialDelivery: DeliveryConfiguration = {
  pipeline_id: "",
  name: "Primary delivery",
  write_mode: "upsert",
  primary_key_mode: "record_key",
  auto_create: true,
  auto_evolve: true,
  delete_enabled: true,
  null_handling: "ignore",
  batch_size: 500,
  max_retries: 5,
  retry_backoff_ms: 1000,
  tasks_max: 1,
};

export function Unavailable({
  notice = "A sink metrics provider is required to measure successful writes.",
}: {
  notice?: string;
}) {
  return (
    <span className="muted" title={notice}>
      Unavailable
    </span>
  );
}

export const databaseName = (type: Destination["type"]) =>
  type === "mysql" ? "MySQL" : "PostgreSQL";

export function ConnectionFields({
  form,
  existing = false,
}: {
  form: ReturnType<typeof useForm<DestinationForm>>;
  existing?: boolean;
}) {
  const errors = form.formState.errors;
  const providers = useQuery({
    queryKey: ["database-providers"],
    queryFn: () => api<DatabaseProviderMetadata[]>("/database-providers"),
  });
  return (
    <div className="form-grid">
      <Field label="Destination name" error={errors.name?.message}>
        <Input {...form.register("name")} />
      </Field>
      <Field label="Environment">
        <select {...form.register("environment")}>
          <option>DEV</option>
          <option>STAGING</option>
          <option>PROD</option>
        </select>
      </Field>
      <Field label="Database type">
        <select
          {...form.register("type")}
          disabled={existing}
          onChange={(event) => {
            const type = event.target.value as DestinationForm["type"];
            form.setValue("type", type);
            const provider = providers.data?.find((item) => item.type === type);
            if (provider) form.setValue("port", provider.default_port);
          }}
        >
          {(providers.data || [])
            .filter((item) => item.destination_supported)
            .map((provider) => (
              <option key={provider.type} value={provider.type}>
                {provider.display_name}
              </option>
            ))}
        </select>
      </Field>
      <Field label="Host" error={errors.host?.message}>
        <Input {...form.register("host")} placeholder="destination-postgres" />
      </Field>
      <Field label="Port" error={errors.port?.message}>
        <Input
          type="number"
          {...form.register("port", { valueAsNumber: true })}
        />
      </Field>
      <Field label="Database" error={errors.database_name?.message}>
        <Input {...form.register("database_name")} placeholder="analytics" />
      </Field>
      <Field label="Username" error={errors.username?.message}>
        <Input
          {...form.register("username")}
          placeholder="delivery_user"
          autoComplete="off"
        />
      </Field>
      <Field
        label="Password"
        error={errors.password?.message}
        hint={
          existing
            ? "Leave blank to keep the current password."
            : "Encrypted in metadata; never returned by the API."
        }
      >
        <Input
          type="password"
          {...form.register("password")}
          autoComplete="new-password"
        />
      </Field>
      <Field label="SSL">
        <select
          value={form.watch("ssl_enabled") ? "on" : "off"}
          onChange={(event) =>
            form.setValue("ssl_enabled", event.target.value === "on")
          }
        >
          <option value="off">Off</option>
          <option value="on">Verify certificate and hostname</option>
        </select>
      </Field>
      <div className="full-width">
        <Field label="Description">
          <Input {...form.register("description")} />
        </Field>
      </div>
    </div>
  );
}
