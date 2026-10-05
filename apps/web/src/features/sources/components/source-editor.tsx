"use client";

import { ErrorPanel, Field } from "@/components/common";
import { Panel } from "@/components/operational";
import { api } from "@/lib/api";
import { SourceForm, sourceSchema } from "@/lib/validation";
import type { DatabaseProviderMetadata, Source } from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";

export function SourceEditor({
  source,
  open,
  onOpenChange,
  fullPage = false,
}: {
  fullPage?: boolean;
  source?: Source;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const client = useQueryClient();
  const providers = useQuery({
    queryKey: ["database-providers"],
    queryFn: () => api<DatabaseProviderMetadata[]>("/database-providers"),
  });
  const form = useForm<SourceForm>({
    resolver: zodResolver(sourceSchema),
    defaultValues: source
      ? { ...source, password: "" }
      : {
          name: "Commerce PostgreSQL",
          type: "postgresql",
          environment: "development",
          host: "cdc-source-postgres",
          port: 5432,
          database_name: "commerce",
          username: "cdc_user",
          password: "",
          ssl_enabled: false,
          provider_options: { connection_timeout_seconds: 10 },
        },
  });
  const selectedType = useWatch({ control: form.control, name: "type" });
  const save = useMutation({
    mutationFn: (values: SourceForm) =>
      api<Source>(source ? `/sources/${source.id}` : "/sources", {
        method: source ? "PUT" : "POST",
        body: JSON.stringify({
          ...values,
          password: values.password || undefined,
        }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["sources"] });
      client.invalidateQueries({ queryKey: ["source"] });
      client.invalidateQueries({ queryKey: ["source-tables"] });
      toast.success(source ? "Source updated" : "Source registered");
      onOpenChange(false);
    },
    onError: (e) => toast.error(e.message),
  });
  const errors = form.formState.errors;
  const content = (
    <form
      onSubmit={form.handleSubmit((v) => {
        if (!source && !v.password) {
          form.setError("password", { message: "Password is required" });
          return;
        }
        save.mutate(v);
      })}
      className="form-grid"
    >
      <Field label="Source name" error={errors.name?.message}>
        <Input {...form.register("name")} />
      </Field>
      <Field label="Environment" error={errors.environment?.message}>
        <select {...form.register("environment")}>
          <option>development</option>
          <option>staging</option>
          <option>production</option>
        </select>
      </Field>
      <Field
        label="Database type"
        hint="Choose the CDC engine explicitly; ports do not determine it."
      >
        <select
          {...form.register("type")}
          onChange={(event) => {
            const type = event.target.value as SourceForm["type"];
            form.setValue("type", type);
            const provider = providers.data?.find((item) => item.type === type);
            if (provider) form.setValue("port", provider.default_port);
          }}
        >
          {(providers.data || [])
            .filter((item) => item.source_supported)
            .map((provider) => (
              <option key={provider.type} value={provider.type}>
                {provider.display_name}
              </option>
            ))}
        </select>
      </Field>
      <Field label="Database" error={errors.database_name?.message}>
        <Input {...form.register("database_name")} />
      </Field>
      <Field
        label="Host"
        error={errors.host?.message}
        hint="Use the hostname reachable from the API and Connect workers."
      >
        <Input {...form.register("host")} />
      </Field>
      <Field label="Port" error={errors.port?.message}>
        <Input
          type="number"
          {...form.register("port", { valueAsNumber: true })}
        />
      </Field>
      <Field label="Username" error={errors.username?.message}>
        <Input autoComplete="off" {...form.register("username")} />
      </Field>
      <Field
        label={source ? "Password (blank keeps current)" : "Password"}
        error={errors.password?.message}
      >
        <Input
          type="password"
          autoComplete="new-password"
          {...form.register("password")}
        />
      </Field>
      <label className="checkbox-row full-width">
        <input type="checkbox" {...form.register("ssl_enabled")} />
        Verify TLS connection (server certificate must be trusted)
      </label>
      <details className="full-width">
        <summary>Advanced settings</summary>
        <div className="form-grid">
          {selectedType === "mysql" && (
            <Field
              label="Server ID"
              hint="Leave blank to allocate and persist a unique ID."
            >
              <Input
                type="number"
                {...form.register("provider_options.server_id", {
                  setValueAs: (value) =>
                    value === "" ? undefined : Number(value),
                })}
              />
            </Field>
          )}
          <Field label="Connection timeout (seconds)">
            <Input
              type="number"
              {...form.register("provider_options.connection_timeout_seconds", {
                valueAsNumber: true,
              })}
            />
          </Field>
        </div>
      </details>
      {save.isError && (
        <div className="full-width">
          <ErrorPanel error={save.error} />
        </div>
      )}
      <div className="dialog-actions full-width">
        <Button
          type="button"
          variant="outline"
          onClick={() => onOpenChange(false)}
        >
          Cancel
        </Button>
        <Button disabled={save.isPending}>
          {save.isPending && <Loader2 size={15} className="spin" />}
          {source ? "Save changes" : "Register source"}
        </Button>
      </div>
    </form>
  );
  return fullPage ? (
    <div className="source-form-page">
      <Panel
        title="Connection details"
        description="Database credentials are encrypted in the control plane and are never returned."
      >
        {content}
      </Panel>
    </div>
  ) : (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={source ? "Edit source" : "Register a source"}
      description="Connect a supported database. Credentials are encrypted in the control plane."
    >
      {content}
    </Dialog>
  );
}
