"use client";

import {
  ErrorPanel,
  Field,
  JsonView,
  Loading,
  PageHeader,
  Status,
} from "@/components/common";
import { api, post } from "@/lib/api";
import type {
  Connection,
  ConnectionCategory,
  ConnectionProvider,
  ConnectionProviderMetadata,
  ConnectionTestResult,
} from "@cluecdc/contracts";
import { Button, Input } from "@cluecdc/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Server } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  categoryCopy,
  FormState,
  initialState,
  providerCategory,
  ProviderFields,
  providerName,
} from "./shared";

export function ConnectionWizard() {
  const router = useRouter();
  const [provider, setProvider] = useState<ConnectionProvider | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [phase, setPhase] = useState(0);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(
    null,
  );
  const providers = useQuery({
    queryKey: ["connection-providers"],
    queryFn: () => api<ConnectionProviderMetadata[]>("/connection-providers"),
  });
  const payload = () => {
    if (!provider || !form) throw new Error("Choose a provider");
    const config = Object.fromEntries(
      Object.entries(form.config).filter(([, value]) => value !== ""),
    );
    const credentials = Object.fromEntries(
      Object.entries(form.credentials).filter(([, value]) => value !== ""),
    );
    return {
      ...form,
      config,
      credentials,
      provider,
      category: providerCategory(provider),
      capabilities:
        providerCategory(provider) === "OBJECT_STORAGE"
          ? ["DESTINATION"]
          : undefined,
    };
  };
  const create = useMutation({
    mutationFn: () => post<Connection>("/connections", payload()),
    onSuccess: (value) => router.push(`/connections/${value.id}`),
  });
  const testDraft = useMutation({
    mutationFn: () =>
      post<ConnectionTestResult>("/connections/test", payload()),
    onSuccess: (result) => setTestResult(result),
  });
  const select = (value: ConnectionProvider) => {
    setProvider(value);
    setForm(initialState(value));
    setPhase(1);
    setTestResult(null);
  };
  const config = (key: string, value: string | number | boolean) =>
    setForm((old) =>
      old ? { ...old, config: { ...old.config, [key]: value } } : old,
    );
  const secret = (key: string, value: string) =>
    setForm((old) =>
      old ? { ...old, credentials: { ...old.credentials, [key]: value } } : old,
    );
  return (
    <>
      <PageHeader
        title="New Connection"
        description={
          provider
            ? `Configure ${providerName(provider)}.`
            : "What do you want to connect?"
        }
        eyebrow="CONNECTIONS / NEW"
      />
      <div className="wizard-steps" aria-label="Connection creation steps">
        {[
          "Choose Type",
          "Connection Details",
          "Authentication",
          "Advanced Options",
          "Test Connection",
          "Review",
        ].map((label, index) => (
          <div
            key={label}
            className={
              index === phase ? "current" : index < phase ? "done" : ""
            }
          >
            <span>{index + 1}</span>
            {label}
          </div>
        ))}
      </div>
      {!provider || phase === 0 ? (
        providers.isPending ? (
          <Loading />
        ) : providers.isError ? (
          <ErrorPanel error={providers.error} />
        ) : (
          <div className="provider-groups">
            {(Object.keys(categoryCopy) as ConnectionCategory[]).map(
              (category) => (
                <section className="panel" key={category}>
                  <div className="panel-heading">
                    <div>
                      <h2>{categoryCopy[category].title}</h2>
                      <p>{categoryCopy[category].description}</p>
                    </div>
                  </div>
                  <div className="provider-grid">
                    {providers.data
                      ?.filter((item) => item.category === category)
                      .map((item) => (
                        <button
                          key={item.provider}
                          onClick={() => select(item.provider)}
                        >
                          <Server size={20} />
                          <strong>{item.name}</strong>
                          <span>{item.description}</span>
                        </button>
                      ))}
                  </div>
                </section>
              ),
            )}
          </div>
        )
      ) : phase === 1 ? (
        form && (
          <section className="panel source-form-page">
            <div className="panel-body">
              <div className="form-grid" onChange={() => setTestResult(null)}>
                <Field label="Connection name">
                  <Input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </Field>
                <Field label="Description">
                  <Input
                    value={form.description}
                    onChange={(e) =>
                      setForm({ ...form, description: e.target.value })
                    }
                  />
                </Field>
                <ProviderFields
                  provider={provider}
                  form={form}
                  config={config}
                  secret={secret}
                />
              </div>
              <div className="wizard-actions">
                <Button
                  variant="outline"
                  onClick={() => {
                    setProvider(null);
                    setForm(null);
                    setPhase(0);
                  }}
                >
                  Back
                </Button>
                <Button disabled={!form.name} onClick={() => setPhase(4)}>
                  Continue to test
                </Button>
              </div>
            </div>
          </section>
        )
      ) : phase === 4 ? (
        <section className="panel source-form-page">
          <div className="panel-body">
            <h2>Test Connection</h2>
            <p className="muted">
              ClueCDC validates network access, authentication, and
              provider-specific permissions without saving credentials.
            </p>
            {testDraft.isError && <ErrorPanel error={testDraft.error} />}
            {testResult && (
              <div className="diagnostic-list">
                {testResult.checks.map((check) => (
                  <div key={check.name}>
                    <strong>{check.name.replaceAll("_", " ")}</strong>
                    <Status value={check.status} />
                    <span>{check.message || "Passed"}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="wizard-actions">
              <Button variant="outline" onClick={() => setPhase(1)}>
                Back
              </Button>
              <Button
                variant="outline"
                disabled={testDraft.isPending}
                onClick={() => testDraft.mutate()}
              >
                Test Connection
              </Button>
              <Button onClick={() => setPhase(5)}>Review</Button>
            </div>
          </div>
        </section>
      ) : (
        form && (
          <section className="panel source-form-page">
            <div className="panel-body">
              <h2>Review</h2>
              <dl className="facts">
                <dt>Name</dt>
                <dd>{form.name}</dd>
                <dt>Type</dt>
                <dd>{providerName(provider)}</dd>
                <dt>Category</dt>
                <dd>{categoryCopy[providerCategory(provider)].title}</dd>
                <dt>Credentials</dt>
                <dd>Configured and encrypted on save</dd>
              </dl>
              <details className="advanced">
                <summary>Non-sensitive configuration</summary>
                <JsonView value={form.config} />
              </details>
              {create.isError && <ErrorPanel error={create.error} />}
              <div className="wizard-actions">
                <Button variant="outline" onClick={() => setPhase(4)}>
                  Back
                </Button>
                <Button
                  disabled={create.isPending}
                  onClick={() => create.mutate()}
                >
                  Create Connection
                </Button>
              </div>
            </div>
          </section>
        )
      )}
    </>
  );
}
