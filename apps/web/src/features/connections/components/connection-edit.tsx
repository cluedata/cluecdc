"use client";

import { ErrorPanel, Field, Loading, PageHeader } from "@/components/common";
import { ConnectionTestControl } from "@/components/connection-test-control";
import { api, post } from "@/lib/api";
import type { ConnectionTestFeedback } from "@/lib/connection-test";
import type { Connection } from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { FormState, initialState, ProviderFields } from "./shared";

export function ConnectionEditPage({ id }: { id: string }) {
  const connection = useQuery({
    queryKey: ["connection", id],
    queryFn: () => api<Connection>(`/connections/${id}`),
  });
  if (connection.isPending) return <Loading />;
  if (connection.isError) return <ErrorPanel error={connection.error} />;
  return (
    <ConnectionEditor
      key={connection.data.updated_at}
      value={connection.data}
    />
  );
}

export function ConnectionEditor({ value }: { value: Connection }) {
  const router = useRouter();
  const defaults = initialState(value.provider);
  const [form, setForm] = useState<FormState>({
    name: value.name,
    description: value.description,
    config: {
      ...defaults.config,
      ...value.config,
    },
    credentials: defaults.credentials,
  });
  const payload = () => ({
    ...form,
    credentials: Object.fromEntries(
      Object.entries(form.credentials).filter(([, secret]) => secret),
    ),
    provider: value.provider,
    category: value.category,
    capabilities: value.capabilities,
  });
  const save = useMutation({
    mutationFn: () =>
      api<Connection>(`/connections/${value.id}`, {
        method: "PUT",
        body: JSON.stringify(payload()),
      }),
    onSuccess: () => router.push(`/connections/${value.id}`),
  });
  const config = (key: string, next: string | number | boolean) =>
    setForm((old) => ({ ...old, config: { ...old.config, [key]: next } }));
  const secret = (key: string, next: string) =>
    setForm((old) => ({
      ...old,
      credentials: { ...old.credentials, [key]: next },
    }));
  return (
    <>
      <PageHeader
        title={`Edit ${value.name}`}
        description="Update non-sensitive settings or replace selected credentials. Saved secrets are never returned."
        eyebrow="CONNECTIONS / EDIT"
      />
      <section className="panel source-form-page">
        <div className="panel-body">
          <div className="form-grid">
            <Field label="Connection name">
              <Input
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
              provider={value.provider}
              form={form}
              config={config}
              secret={secret}
            />
          </div>
          <p className="muted">
            Leave credential fields blank to test with saved credentials.
          </p>
          <ConnectionTestControl
            fingerprint={JSON.stringify(payload())}
            disabled={!form.name.trim() || save.isPending}
            run={() =>
              post<ConnectionTestFeedback>(
                `/connections/test?identifier=${value.id}`,
                payload(),
              )
            }
          />
          {save.isError && <ErrorPanel error={save.error} />}
          <div className="wizard-actions">
            <Button variant="outline" onClick={() => router.back()}>
              Cancel
            </Button>
            <Button
              disabled={!form.name || save.isPending}
              onClick={() => save.mutate()}
            >
              Save changes
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
