"use client";

import {
  ErrorPanel,
  JsonView,
  Loading,
  PageHeader,
  Status,
} from "@/components/common";
import { api, date, post } from "@/lib/api";
import type { Connection, ConnectionTestResult } from "@cluecdc/contracts";
import { Button } from "@cluecdc/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { categoryCopy, providerName } from "./shared";
import { useAuthorization } from "@/lib/auth";

export function ConnectionDetailPage({ id }: { id: string }) {
  const { can } = useAuthorization();
  const canWrite = can("destinations.write");
  const queryClient = useQueryClient();
  const [checks, setChecks] = useState<ConnectionTestResult | null>(null);
  const connection = useQuery({
    queryKey: ["connection", id],
    queryFn: () => api<Connection>(`/connections/${id}`),
  });
  const test = useMutation({
    mutationFn: () => post<ConnectionTestResult>(`/connections/${id}/test`),
    onSuccess: (value) => {
      setChecks(value);
      queryClient.invalidateQueries({ queryKey: ["connection", id] });
    },
  });
  if (connection.isPending) return <Loading />;
  if (connection.isError) return <ErrorPanel error={connection.error} />;
  const value = connection.data;
  return (
    <>
      <PageHeader
        title={value.name}
        description={value.description || providerName(value.provider)}
        eyebrow={`CONNECTIONS / ${categoryCopy[value.category].title.toUpperCase()}`}
      >
        {canWrite && (
          <>
            <Button asChild variant="outline">
              <Link href={`/connections/${id}/edit`}>Edit</Link>
            </Button>
            <Button disabled={test.isPending} onClick={() => test.mutate()}>
              Test Connection
            </Button>
          </>
        )}
        {value.category === "OBJECT_STORAGE" && can("deliveries.write") && (
          <Button asChild>
            <Link href={`/deliveries/new/object-storage?destination_id=${id}`}>
              Create delivery
            </Link>
          </Button>
        )}
      </PageHeader>
      {test.isError && <ErrorPanel error={test.error} />}
      <section className="resource-summary">
        <div className="metric-card">
          <span className="metric-title">Status</span>
          <Status value={value.status} />
        </div>
        <div className="metric-card">
          <span className="metric-title">Provider</span>
          <strong>{providerName(value.provider)}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-title">Last tested</span>
          <strong className="summary-date">{date(value.last_tested_at)}</strong>
        </div>
        <div className="metric-card">
          <span className="metric-title">Credentials</span>
          <strong>
            {value.credentials.configured
              ? value.credentials.masked_value
              : "Not configured"}
          </strong>
        </div>
      </section>
      {checks && (
        <section className="panel">
          <div className="panel-heading">
            <h2>Connection diagnostics</h2>
          </div>
          <div className="diagnostic-list">
            {checks.checks.map((check) => (
              <div key={check.name}>
                <CheckCircle2 size={17} />
                <strong>{check.name.replaceAll("_", " ")}</strong>
                <Status value={check.status} />
                <span>{check.message}</span>
              </div>
            ))}
          </div>
        </section>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Used by</h2>
            <p>Pipelines and deliveries using this connection.</p>
          </div>
        </div>
        {value.used_by?.length ? (
          <div className="audit-list">
            {value.used_by.map((dependency) => (
              <div key={`${dependency.type}-${dependency.id}`}>
                <Status value={dependency.type} />
                <strong>{dependency.name}</strong>
                <span>
                  {dependency.pipeline || dependency.type.replaceAll("_", " ")}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">
            This connection is not used by another resource.
          </p>
        )}
      </section>
      <details className="advanced">
        <summary>Non-sensitive connection configuration</summary>
        <JsonView value={value.config} />
      </details>
    </>
  );
}
