"use client";

import { ErrorPanel, Field, Loading } from "@/components/common";
import { ConnectionTestControl } from "@/components/connection-test-control";
import { api, post } from "@/lib/api";
import type { ConnectionTestFeedback } from "@/lib/connection-test";
import type {
  Connection,
  ConnectionProvider,
  ConnectionProviderMetadata,
} from "@cluecdc/contracts";
import { Button, Dialog, Input } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  FormState,
  initialState,
  providerCategory,
  ProviderFields,
} from "./shared";

type Capability = "SOURCE" | "DESTINATION";

function stateFor(value: Connection | null, provider: ConnectionProvider) {
  const defaults = initialState(provider);
  if (!value) return defaults;
  return {
    name: value.name,
    description: value.description,
    config: { ...defaults.config, ...value.config },
    credentials: defaults.credentials,
  };
}

export function ConnectionDialog({
  open,
  onOpenChange,
  value,
  capability,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: Connection | null;
  capability?: Capability;
}) {
  const queryClient = useQueryClient();
  const providers = useQuery({
    queryKey: ["connection-providers"],
    queryFn: () => api<ConnectionProviderMetadata[]>("/connection-providers"),
    enabled: open,
  });
  const availableProviders = useMemo(
    () =>
      (providers.data || []).filter(
        (item) => capability !== "SOURCE" || item.category === "DATABASE",
      ),
    [capability, providers.data],
  );
  const [provider, setProvider] = useState<ConnectionProvider>(
    value?.provider || "POSTGRESQL",
  );
  const [form, setForm] = useState<FormState>(() =>
    stateFor(value, value?.provider || "POSTGRESQL"),
  );

  const capabilities: Capability[] = value
    ? value.capabilities
    : capability
      ? [capability]
      : providerCategory(provider) === "OBJECT_STORAGE"
        ? ["DESTINATION"]
        : ["SOURCE", "DESTINATION"];
  const payload = () => ({
    ...form,
    config: Object.fromEntries(
      Object.entries(form.config).filter(([, entry]) => entry !== ""),
    ),
    credentials: Object.fromEntries(
      Object.entries(form.credentials).filter(([, entry]) => entry !== ""),
    ),
    provider,
    category: providerCategory(provider),
    capabilities,
  });
  const save = useMutation({
    mutationFn: () =>
      value
        ? api<Connection>(`/connections/${value.id}`, {
            method: "PUT",
            body: JSON.stringify(payload()),
          })
        : post<Connection>("/connections", payload()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["connections"] });
      if (value) {
        queryClient.invalidateQueries({
          queryKey: ["connection", value.id],
        });
      }
      toast.success(value ? "Connection updated" : "Connection created");
      onOpenChange(false);
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const config = (key: string, next: string | number | boolean) =>
    setForm((old) => ({ ...old, config: { ...old.config, [key]: next } }));
  const secret = (key: string, next: string) =>
    setForm((old) => ({
      ...old,
      credentials: { ...old.credentials, [key]: next },
    }));
  const changeProvider = (next: ConnectionProvider) => {
    setProvider(next);
    setForm((old) => ({ ...initialState(next), name: old.name }));
    save.reset();
  };
  const subject =
    capability === "SOURCE"
      ? "source"
      : capability === "DESTINATION"
        ? "destination"
        : "connection";

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`${value ? "Edit" : "Add"} ${subject}`}
      description="Configure the endpoint and verify access before saving."
    >
      {providers.isPending ? (
        <Loading />
      ) : providers.isError ? (
        <ErrorPanel error={providers.error} retry={() => providers.refetch()} />
      ) : (
        <form
          className="form-grid"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Connection type">
            <select
              value={provider}
              disabled={!!value}
              onChange={(event) =>
                changeProvider(event.target.value as ConnectionProvider)
              }
            >
              {availableProviders.map((item) => (
                <option key={item.provider} value={item.provider}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Connection name">
            <Input
              required
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
            />
          </Field>
          <Field label="Description">
            <Input
              value={form.description}
              onChange={(event) =>
                setForm({ ...form, description: event.target.value })
              }
            />
          </Field>
          <ProviderFields
            provider={provider}
            form={form}
            config={config}
            secret={secret}
          />
          {value && (
            <p className="muted full-width">
              Leave credential fields blank to keep the stored values.
            </p>
          )}
          <ConnectionTestControl
            fingerprint={JSON.stringify(payload())}
            disabled={!form.name.trim() || save.isPending}
            run={() =>
              post<ConnectionTestFeedback>(
                `/connections/test${value ? `?identifier=${value.id}` : ""}`,
                payload(),
              )
            }
          />
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
            <Button disabled={!form.name.trim() || save.isPending}>
              {save.isPending ? "Saving…" : "Save connection"}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
