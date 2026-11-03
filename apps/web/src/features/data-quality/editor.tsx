"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Input } from "@cluecdc/ui";
import {
  ErrorPanel,
  Field,
  JsonView,
  Loading,
  PageHeader,
} from "@/components/common";
import { api, post } from "@/lib/api";
import { useAuthorization } from "@/lib/auth";
import { Confidence } from "./pages";
import {
  Capability,
  Check,
  Column,
  Configuration,
  Connection,
  Dataset,
  Predicate,
  RuleConfig,
  defaultRule,
  emptyDataset,
  explanations,
  label,
  newConfiguration,
  ruleNames,
} from "./types";

const base = "/data-quality";
const steps = ["Datasets", "Rules", "Schedule", "Alerting", "Review"];
const frequencies = [60, 300, 600, 900, 1800, 3600, 21600, 86400];

function Toggle({
  name,
  checked,
  onChange,
}: {
  name: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="dq-toggle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {name}
    </label>
  );
}

function DatasetPicker({
  side,
  value,
  connections,
  onChange,
}: {
  side: "source" | "destination";
  value: Dataset;
  connections: Connection[];
  onChange: (d: Dataset) => void;
}) {
  const tables = useQuery({
    queryKey: ["data-quality", "tables", value.connection_id],
    queryFn: () =>
      api<{ tables: { schema_name: string; table_name: string }[] }>(
        `${base}/connections/${value.connection_id}/tables`,
      ),
    enabled: !!value.connection_id,
  });
  const rows = tables.data?.tables || [];
  const sideName = label(side);
  return (
    <section className="panel dq-panel">
      <h2>{sideName}</h2>
      <Field label={`${sideName} Connection`}>
        <select
          value={value.connection_id}
          onChange={(e) => {
            const c = connections.find((c) => c.id === e.target.value);
            onChange({
              ...emptyDataset(),
              connection_id: c?.id || "",
              database: c?.database || "",
            });
          }}
        >
          <option value="">Select a connection</option>
          {connections
            .filter((c) => c.capabilities.includes(side.toUpperCase()))
            .map((c) => (
              <option key={c.id} value={c.id} disabled={!c.supported}>
                {c.name} · {c.provider}
                {!c.supported ? ` — ${c.reason}` : ""}
              </option>
            ))}
        </select>
      </Field>
      <Field
        label={`${sideName} database`}
        hint="Only the registered database is authorized. Register another Connection to inspect another database."
      >
        <select value={value.database} disabled>
          <option>{value.database || "Select a connection first"}</option>
        </select>
      </Field>
      {tables.isLoading && <Loading />}
      {tables.error && (
        <ErrorPanel error={tables.error} retry={() => tables.refetch()} />
      )}
      <Field label={`${sideName} schema`}>
        <select
          value={value.schema_name}
          onChange={(e) =>
            onChange({ ...value, schema_name: e.target.value, table: "" })
          }
        >
          <option value="">Select a schema</option>
          {[...new Set(rows.map((t) => t.schema_name))].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </Field>
      <Field label={`${sideName} table`}>
        <select
          value={value.table}
          onChange={(e) => onChange({ ...value, table: e.target.value })}
        >
          <option value="">Select a table</option>
          {rows
            .filter((t) => t.schema_name === value.schema_name)
            .map((t) => (
              <option key={t.table_name}>{t.table_name}</option>
            ))}
        </select>
      </Field>
      {tables.isSuccess && !rows.length && (
        <p>No readable base tables discovered.</p>
      )}
      <Toggle
        name={`${sideName} represents current-state rows with comparable business meaning`}
        checked={value.current_state_confirmed}
        onChange={(v) => onChange({ ...value, current_state_confirmed: v })}
      />
      <p>
        Append-only CDC history and SCD tables are unsupported. Selecting
        arbitrary tables does not establish a replication relationship.
      </p>
    </section>
  );
}

function RuleEditor({
  rule,
  source,
  destination,
  onChange,
}: {
  rule: RuleConfig;
  source: Column[];
  destination: Column[];
  onChange: (r: RuleConfig) => void;
}) {
  const set = (key: string, value: RuleConfig[string]) =>
    onChange({ ...rule, [key]: value });
  const number = (key: string, name: string, fallback: number) => (
    <Field label={name} key={key}>
      <Input
        type="number"
        min="0"
        value={Number(rule[key] ?? fallback)}
        onChange={(e) => set(key, Number(e.target.value))}
      />
    </Field>
  );
  const select = (
    key: string,
    name: string,
    values: string[],
    fallback: string,
  ) => (
    <Field label={name} key={key}>
      <select
        value={String(rule[key] ?? fallback)}
        onChange={(e) => set(key, e.target.value)}
      >
        {values.map((v) => (
          <option key={v} value={v}>
            {label(v)}
          </option>
        ))}
      </select>
    </Field>
  );
  return (
    <div className="dq-rule-options">
      <Toggle
        name="Rule enabled"
        checked={rule.enabled !== false}
        onChange={(v) => set("enabled", v)}
      />
      {rule.type === "row_count_diff" && (
        <>
          {select(
            "mode",
            "Tolerance mode",
            ["ABSOLUTE", "PERCENTAGE"],
            "ABSOLUTE",
          )}
          {number("tolerance", "Maximum difference", 0)}
          {select(
            "direction",
            "Allowed direction",
            ["BOTH", "DESTINATION_BEHIND", "DESTINATION_AHEAD"],
            "BOTH",
          )}
        </>
      )}
      {["last_updated", "freshness_delay", "data_age"].includes(rule.type) && (
        <>
          {(["source", "destination"] as const).map((side) => (
            <div key={side}>
              <Field label={`${label(side)} timestamp column`}>
                <select
                  value={String(rule[`${side}_column`] || "")}
                  onChange={(e) => set(`${side}_column`, e.target.value)}
                >
                  <option value="">Select timestamp column</option>
                  {(side === "source" ? source : destination)
                    .filter((c) => c.canonical.family === "timestamp")
                    .map((c) => (
                      <option key={c.column_name}>{c.column_name}</option>
                    ))}
                </select>
              </Field>
              <Field
                label={`${label(side)} timestamp timezone`}
                hint="Declare the actual timezone of naive timestamps. MySQL TIMESTAMP is read in UTC."
              >
                <Input
                  value={String(rule[`${side}_timezone`] || "")}
                  placeholder="IANA timezone, e.g. UTC"
                  onChange={(e) =>
                    set(`${side}_timezone`, e.target.value || undefined)
                  }
                />
              </Field>
            </div>
          ))}
          {select(
            "semantics",
            "Shared timestamp semantics",
            ["BUSINESS_UPDATED", "INGESTION_TIME"],
            "BUSINESS_UPDATED",
          )}
          <p>
            Both selected columns must mean the same thing. A business-update
            timestamp cannot be compared to an ingestion timestamp.
          </p>
          {select(
            "null_policy",
            "Empty / NULL timestamps",
            ["INCONCLUSIVE", "FAIL"],
            "INCONCLUSIVE",
          )}
          {number(
            "future_tolerance_seconds",
            "Future timestamp / clock skew allowance (seconds)",
            30,
          )}
        </>
      )}
      {rule.type === "last_updated" && (
        <>
          {select(
            "mode",
            "Timestamp comparison",
            ["EXACT", "WITHIN_TOLERANCE"],
            "WITHIN_TOLERANCE",
          )}
          {number(
            "tolerance_seconds",
            "Maximum timestamp difference (seconds)",
            0,
          )}
        </>
      )}
      {rule.type === "freshness_delay" && (
        <>
          {number("max_delay_seconds", "Maximum delay (seconds)", 300)}
          {select(
            "source_idle_policy",
            "Source idle policy",
            ["INCONCLUSIVE", "EVALUATE"],
            "INCONCLUSIVE",
          )}
          {number(
            "activity_window_seconds",
            "Source activity window (seconds)",
            600,
          )}
        </>
      )}
      {rule.type === "data_age" && (
        <>
          {number("max_age_seconds", "Maximum data age (seconds)", 600)}
          {select(
            "activity_policy",
            "Activity policy",
            ["ONLY_WHEN_SOURCE_ACTIVE", "EXPECT_NEW_DATA"],
            "ONLY_WHEN_SOURCE_ACTIVE",
          )}
          <p>
            Only When Source Active requires a recent business update at Source.
            Inactive or unknown activity produces INCONCLUSIVE. Expect New Data
            treats a stale Destination as a failure even when Source is idle.
          </p>
          {number(
            "activity_window_seconds",
            "Source activity window (seconds)",
            600,
          )}
        </>
      )}
      {rule.type === "schema_match" && (
        <>
          {(
            [
              ["compare_names", true],
              ["compare_types", true],
              ["compare_nullability", true],
              ["compare_order", false],
              ["compare_primary_key", false],
              ["allow_extra_destination", true],
              ["allow_missing_source", false],
            ] as const
          ).map(([key, fallback]) => (
            <Toggle
              key={key}
              name={label(key)}
              checked={Boolean(rule[key] ?? fallback)}
              onChange={(v) => set(key, v)}
            />
          ))}
          {select(
            "type_mode",
            "Type comparison",
            ["EXACT", "COMPATIBLE"],
            "COMPATIBLE",
          )}
          <h4>Column mapping / exclusions</h4>
          {source.map((c) => {
            const mapping = (rule.column_mapping || {}) as Record<
              string,
              string
            >;
            const excluded = (rule.excluded_columns || []) as string[];
            return (
              <div key={c.column_name}>
                <Field label={`Map ${c.column_name}`}>
                  <select
                    value={mapping[c.column_name] || c.column_name}
                    onChange={(e) =>
                      set("column_mapping", {
                        ...mapping,
                        [c.column_name]: e.target.value,
                      })
                    }
                  >
                    <option value={c.column_name}>
                      Same name: {c.column_name}
                    </option>
                    {destination
                      .filter((d) => d.column_name !== c.column_name)
                      .map((d) => (
                        <option key={d.column_name}>{d.column_name}</option>
                      ))}
                  </select>
                </Field>
                <Toggle
                  name={`Exclude ${c.column_name}`}
                  checked={excluded.includes(c.column_name)}
                  onChange={(v) =>
                    set(
                      "excluded_columns",
                      v
                        ? [...excluded, c.column_name]
                        : excluded.filter((n) => n !== c.column_name),
                    )
                  }
                />
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

function Filters({
  side,
  columns,
  value,
  onChange,
}: {
  side: string;
  columns: Column[];
  value: Predicate[];
  onChange: (p: Predicate[]) => void;
}) {
  return (
    <section>
      <h3>{label(side)} filters</h3>
      {value.map((p, index) => (
        <div className="dq-filter-row" key={index}>
          <select
            aria-label={`${side} filter column ${index + 1}`}
            value={p.column}
            onChange={(e) =>
              onChange(
                value.map((v, i) =>
                  i === index ? { ...v, column: e.target.value } : v,
                ),
              )
            }
          >
            <option value="">Column</option>
            {columns.map((c) => (
              <option key={c.column_name}>{c.column_name}</option>
            ))}
          </select>
          <select
            aria-label={`${side} filter operator ${index + 1}`}
            value={p.operator}
            onChange={(e) =>
              onChange(
                value.map((v, i) =>
                  i === index ? { ...v, operator: e.target.value } : v,
                ),
              )
            }
          >
            {[
              "eq",
              "ne",
              "gt",
              "gte",
              "lt",
              "lte",
              "is_null",
              "is_not_null",
            ].map((op) => (
              <option key={op}>{op}</option>
            ))}
          </select>
          {!["is_null", "is_not_null"].includes(p.operator) && (
            <Input
              aria-label={`${side} filter value ${index + 1}`}
              value={String(p.value ?? "")}
              onChange={(e) => {
                const boolean =
                  columns.find((c) => c.column_name === p.column)?.canonical
                    .family === "boolean";
                onChange(
                  value.map((v, i) =>
                    i === index
                      ? {
                          ...v,
                          value: boolean
                            ? e.target.value === "true"
                            : e.target.value,
                        }
                      : v,
                  ),
                );
              }}
            />
          )}
          <Button
            variant="outline"
            onClick={() => onChange(value.filter((_, i) => i !== index))}
          >
            Remove
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        disabled={value.length >= 20}
        onClick={() =>
          onChange([...value, { column: "", operator: "eq", value: "" }])
        }
      >
        Add {side} filter
      </Button>
    </section>
  );
}

function Wizard({ initial, id }: { initial: Configuration; id?: string }) {
  const router = useRouter();
  const [form, setForm] = useState(initial);
  const [step, setStep] = useState(0);
  const [validated, setValidated] = useState("");
  const [capability, setCapability] = useState<Capability | null>(null);
  const [runAfterSave, setRunAfterSave] = useState(false);
  const { can } = useAuthorization();
  const connections = useQuery({
    queryKey: ["data-quality", "connections"],
    queryFn: () => api<Connection[]>(`${base}/connections`),
  });
  const channels = useQuery({
    queryKey: ["data-quality", "channels"],
    queryFn: () =>
      api<{ id: string; name: string; enabled: boolean }[]>(`${base}/channels`),
  });
  const sourceColumns = useQuery({
    queryKey: ["data-quality", "columns", form.source],
    queryFn: () =>
      post<Column[]>(`${base}/columns`, {
        ...form.source,
        current_state_confirmed: true,
      }),
    enabled: !!form.source.table,
  });
  const destinationColumns = useQuery({
    queryKey: ["data-quality", "columns", form.destination],
    queryFn: () =>
      post<Column[]>(`${base}/columns`, {
        ...form.destination,
        current_state_confirmed: true,
      }),
    enabled: !!form.destination.table,
  });
  const source = sourceColumns.data || [],
    destination = destinationColumns.data || [];
  const validate = useMutation({
    mutationFn: () => post<Capability>(`${base}/validate`, form),
    onSuccess: (result) => {
      setCapability(result);
      setValidated(JSON.stringify(form));
    },
  });
  const save = useMutation({
    mutationFn: async () => {
      const item = await api<Check>(
        id ? `${base}/checks/${id}` : `${base}/checks`,
        { method: id ? "PATCH" : "POST", body: JSON.stringify(form) },
      );
      if (runAfterSave && can("data_quality.run")) {
        const run = await post<{ id: string }>(
          `${base}/checks/${item.id}/run`,
          { request_key: crypto.randomUUID() },
        );
        return `/data-quality/runs/${run.id}`;
      }
      return `/data-quality/${item.id}`;
    },
    onSuccess: (path) => router.push(path),
  });
  const patch = <K extends keyof Configuration>(
    key: K,
    value: Configuration[K],
  ) => setForm({ ...form, [key]: value });
  const comparison = <K extends keyof Configuration["comparison"]>(
    key: K,
    value: Configuration["comparison"][K],
  ) => patch("comparison", { ...form.comparison, [key]: value });
  const schedule = <K extends keyof Configuration["schedule"]>(
    key: K,
    value: Configuration["schedule"][K],
  ) => patch("schedule", { ...form.schedule, [key]: value });
  const alerting = <K extends keyof Configuration["alerting"]>(
    key: K,
    value: Configuration["alerting"][K],
  ) => patch("alerting", { ...form.alerting, [key]: value });
  const validDatasets =
    form.name.trim() &&
    form.source.table &&
    form.destination.table &&
    form.source.current_state_confirmed &&
    form.destination.current_state_confirmed;
  const isValidated =
    capability?.status === "SUPPORTED" && validated === JSON.stringify(form);
  if (connections.isLoading) return <Loading />;
  if (connections.error) return <ErrorPanel error={connections.error} />;
  return (
    <>
      <PageHeader
        title={id ? "Edit Data Quality Check" : "Create Data Quality Check"}
        description={`Step ${step + 1} of 5 · ${steps[step]}`}
      />
      <Confidence />
      <div className="tabs" aria-label="Check configuration steps">
        {steps.map((name, i) => (
          <button
            key={name}
            className={i === step ? "tab-active" : ""}
            disabled={i > step && (!validDatasets || !form.rules.length)}
            onClick={() => setStep(i)}
          >
            {i + 1}. {name}
          </button>
        ))}
      </div>
      {step === 0 && (
        <>
          <section className="panel dq-panel">
            <Field label="Check name">
              <Input
                value={form.name}
                maxLength={120}
                onChange={(e) => patch("name", e.target.value)}
              />
            </Field>
            <Field label="Description">
              <Input
                value={form.description}
                maxLength={1000}
                onChange={(e) => patch("description", e.target.value)}
              />
            </Field>
            <Toggle
              name="Check enabled"
              checked={form.enabled}
              onChange={(v) => patch("enabled", v)}
            />
          </section>
          <div className="dq-grid">
            <DatasetPicker
              side="source"
              value={form.source}
              connections={connections.data || []}
              onChange={(v) => patch("source", v)}
            />
            <DatasetPicker
              side="destination"
              value={form.destination}
              connections={connections.data || []}
              onChange={(v) => patch("destination", v)}
            />
          </div>
        </>
      )}
      {step === 1 && (
        <>
          {sourceColumns.error && <ErrorPanel error={sourceColumns.error} />}{" "}
          {destinationColumns.error && (
            <ErrorPanel error={destinationColumns.error} />
          )}
          <div className="dq-grid">
            {ruleNames.map((type) => {
              const rule = form.rules.find((r) => r.type === type);
              return (
                <section className="panel dq-panel" key={type}>
                  <Toggle
                    name={label(type)}
                    checked={!!rule}
                    onChange={(v) =>
                      patch(
                        "rules",
                        v
                          ? [...form.rules, defaultRule(type)]
                          : form.rules.filter((r) => r.type !== type),
                      )
                    }
                  />
                  <p>{explanations[type]}</p>
                  {rule && (
                    <RuleEditor
                      rule={rule}
                      source={source}
                      destination={destination}
                      onChange={(updated) =>
                        patch(
                          "rules",
                          form.rules.map((r) =>
                            r.type === type ? updated : r,
                          ),
                        )
                      }
                    />
                  )}
                </section>
              );
            })}
          </div>
        </>
      )}
      {step === 2 && (
        <section className="panel dq-panel">
          <Toggle
            name="Scheduled execution"
            checked={form.schedule.enabled}
            onChange={(v) => schedule("enabled", v)}
          />
          <Field label="Frequency">
            <select
              value={form.schedule.frequency_seconds}
              onChange={(e) =>
                schedule("frequency_seconds", Number(e.target.value))
              }
            >
              {frequencies.map((f) => (
                <option key={f} value={f}>
                  Every {f / 60} minutes
                </option>
              ))}
            </select>
          </Field>
          <Field label="Comparison scope">
            <select
              value={form.comparison.scope}
              onChange={(e) =>
                comparison(
                  "scope",
                  e.target.value as "FULL_TABLE" | "TIME_WINDOW",
                )
              }
            >
              <option value="FULL_TABLE">Full Table</option>
              <option value="TIME_WINDOW">Time Window</option>
            </select>
          </Field>
          {form.comparison.scope === "FULL_TABLE" ? (
            <p>
              Full Table uses the entire eligible dataset. Lookback and settling
              delay do not restrict counts. Full COUNT queries can scan large
              tables.
            </p>
          ) : (
            <>
              <Field label="Period">
                <select
                  value={form.comparison.period}
                  onChange={(e) =>
                    comparison("period", e.target.value as "ROLLING" | "FIXED")
                  }
                >
                  <option value="ROLLING">Rolling window</option>
                  <option value="FIXED" disabled={form.schedule.enabled}>
                    Fixed window (manual only)
                  </option>
                </select>
              </Field>
              {form.comparison.period === "ROLLING" ? (
                <Field label="Lookback (seconds)">
                  <Input
                    type="number"
                    min={60}
                    max={2678400}
                    value={form.comparison.lookback_seconds}
                    onChange={(e) =>
                      comparison("lookback_seconds", Number(e.target.value))
                    }
                  />
                </Field>
              ) : (
                <>
                  {(["fixed_start", "fixed_end"] as const).map((key) => (
                    <Field
                      key={key}
                      label={`${label(key)} (ISO timestamp with timezone)`}
                    >
                      <Input
                        placeholder="2026-11-03T00:00:00Z"
                        value={form.comparison[key] || ""}
                        onChange={(e) => comparison(key, e.target.value)}
                      />
                    </Field>
                  ))}
                </>
              )}
              {(["source", "destination"] as const).map((side) => (
                <div key={side}>
                  <Field label={`${label(side)} window column`}>
                    <select
                      value={form.comparison[`${side}_time_column`] || ""}
                      onChange={(e) =>
                        comparison(`${side}_time_column`, e.target.value)
                      }
                    >
                      <option value="">Select timestamp column</option>
                      {(side === "source" ? source : destination)
                        .filter((c) => c.canonical.family === "timestamp")
                        .map((c) => (
                          <option key={c.column_name}>{c.column_name}</option>
                        ))}
                    </select>
                  </Field>
                  <Field label={`${label(side)} window timezone`}>
                    <Input
                      value={form.comparison[`${side}_timezone`] || ""}
                      onChange={(e) =>
                        comparison(`${side}_timezone`, e.target.value)
                      }
                    />
                  </Field>
                </div>
              ))}
              <Field label="Window timestamp semantics">
                <select
                  value={form.comparison.window_semantics || "BUSINESS_UPDATED"}
                  onChange={(e) =>
                    comparison("window_semantics", e.target.value)
                  }
                >
                  <option value="BUSINESS_UPDATED">
                    Business updated on both sides
                  </option>
                  <option value="INGESTION_TIME">
                    Ingestion time on both sides
                  </option>
                </select>
              </Field>
              <p>
                Window is [start, end), persisted in UTC. Settling delay shifts
                the rolling window into the past. Source activity is assessed
                within the selected scope.
              </p>
            </>
          )}
          <Field label="Settling delay (seconds)">
            <Input
              type="number"
              min={0}
              max={86400}
              value={form.comparison.settling_seconds}
              onChange={(e) =>
                comparison("settling_seconds", Number(e.target.value))
              }
            />
          </Field>
          <Field label="Execution timeout (seconds)">
            <Input
              type="number"
              min={5}
              max={180}
              value={form.schedule.timeout_seconds}
              onChange={(e) =>
                schedule("timeout_seconds", Number(e.target.value))
              }
            />
          </Field>
          <Field label="Display timezone (IANA)">
            <Input
              value={form.schedule.display_timezone}
              onChange={(e) => schedule("display_timezone", e.target.value)}
            />
          </Field>
          <p>
            History timestamps use this display timezone. Execution windows
            persist in UTC.
          </p>
          <Field label="History retention (days)">
            <Input
              type="number"
              min={1}
              max={3650}
              value={form.schedule.retention_days}
              onChange={(e) =>
                schedule("retention_days", Number(e.target.value))
              }
            />
          </Field>
          <Toggle
            name="I acknowledge the cost of one-minute full-table counts"
            checked={form.schedule.full_scan_acknowledged}
            onChange={(v) => schedule("full_scan_acknowledged", v)}
          />
          <Filters
            side="source"
            columns={source}
            value={form.comparison.source_filters}
            onChange={(v) => comparison("source_filters", v)}
          />
          <Filters
            side="destination"
            columns={destination}
            value={form.comparison.destination_filters}
            onChange={(v) => comparison("destination_filters", v)}
          />
        </section>
      )}
      {step === 3 && (
        <section className="panel dq-panel">
          {channels.error && <ErrorPanel error={channels.error} />}
          <Field label="Notification channel">
            <select
              value={form.alerting.channel_id || ""}
              onChange={(e) =>
                patch("alerting", {
                  ...form.alerting,
                  channel_id: e.target.value || null,
                  enabled: e.target.value ? true : false,
                })
              }
            >
              <option value="">No channel</option>
              {channels.data?.map((c) => (
                <option key={c.id} value={c.id} disabled={!c.enabled}>
                  {c.name}
                  {!c.enabled ? " (disabled)" : ""}
                </option>
              ))}
            </select>
          </Field>
          <p>
            Selecting an enabled channel activates alerting. Without an enabled
            channel, notifications are unavailable. Existing channel
            administration permissions still apply.
          </p>
          <Toggle
            name="Alerting enabled"
            checked={!!form.alerting.enabled}
            onChange={(v) => alerting("enabled", v)}
          />
          <Field label="Severity">
            <select
              value={form.alerting.severity}
              onChange={(e) => alerting("severity", e.target.value)}
            >
              {["info", "warning", "critical"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </Field>
          <Field label="Consecutive conclusive failures">
            <Input
              type="number"
              min={1}
              max={100}
              value={form.alerting.failure_threshold}
              onChange={(e) =>
                alerting("failure_threshold", Number(e.target.value))
              }
            />
          </Field>
          <Field label="Cooldown (seconds)">
            <Input
              type="number"
              min={0}
              max={604800}
              value={form.alerting.cooldown_seconds}
              onChange={(e) =>
                alerting("cooldown_seconds", Number(e.target.value))
              }
            />
          </Field>
          {(
            ["send_recovery", "on_fail", "on_error", "on_inconclusive"] as const
          ).map((key) => (
            <Toggle
              key={key}
              name={label(key)}
              checked={form.alerting[key]}
              onChange={(v) => alerting(key, v)}
            />
          ))}
          <p>
            INCONCLUSIVE does not increment quality failure counters or resolve
            incidents. Execution ERROR has a separate incident identity.
          </p>
        </section>
      )}
      {step === 4 && (
        <section className="panel dq-panel">
          <h2>Review configuration</h2>
          <p>
            {form.source.database}.{form.source.schema_name}.{form.source.table}{" "}
            → {form.destination.database}.{form.destination.schema_name}.
            {form.destination.table}
          </p>
          <p>Rules: {form.rules.map((r) => label(r.type)).join(", ")}</p>
          <p>
            {form.schedule.enabled
              ? `Every ${form.schedule.frequency_seconds / 60} minutes`
              : "Manual"}{" "}
            · {label(form.comparison.scope)} · BEST_EFFORT
          </p>
          <JsonView value={form} />
          <Button
            disabled={validate.isPending}
            onClick={() => validate.mutate()}
          >
            Validate capabilities
          </Button>
          {capability && (
            <div role="status">
              <h3>Capability: {capability.status}</h3>
              {capability.rules.map((r) => (
                <p key={r.rule_type}>
                  {label(r.rule_type)}: {r.status} {r.reasons.join("; ")}
                </p>
              ))}
              {capability.warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </div>
          )}
          {can("data_quality.run") && (
            <Toggle
              name="Run Now after saving"
              checked={runAfterSave}
              onChange={setRunAfterSave}
            />
          )}
          <Button
            disabled={!isValidated || save.isPending}
            onClick={() => save.mutate()}
          >
            Save Check
          </Button>
          {!isValidated && (
            <p>Validate the current configuration before saving.</p>
          )}
        </section>
      )}
      {validate.error && <ErrorPanel error={validate.error} />}{" "}
      {save.error && <ErrorPanel error={save.error} />}
      <div className="dq-actions">
        <Button
          variant="outline"
          disabled={!step}
          onClick={() => setStep(step - 1)}
        >
          Back
        </Button>
        {step < 4 && (
          <Button
            disabled={
              step === 0
                ? !validDatasets
                : step === 1
                  ? !form.rules.length
                  : false
            }
            onClick={() => setStep(step + 1)}
          >
            Next
          </Button>
        )}
      </div>
    </>
  );
}

export function CheckEditor({ id }: { id?: string }) {
  const { can, session } = useAuthorization();
  const query = useQuery({
    queryKey: ["data-quality", id, "edit"],
    queryFn: () => api<Check>(`${base}/checks/${id}`),
    enabled: !!id && can("data_quality.write"),
  });
  if (session.isLoading || (id && query.isLoading)) return <Loading />;
  if (!can("data_quality.write"))
    return <p>You do not have permission to edit Data Quality checks.</p>;
  if (query.error) return <ErrorPanel error={query.error} />;
  return (
    <Wizard
      key={id || "new"}
      initial={query.data?.configuration || newConfiguration()}
      id={id}
    />
  );
}
