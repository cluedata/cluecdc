# Data Quality (v0.3.4)

Data Quality compares one Source and one Destination table through registered
Connections. A CDC pipeline is optional; no connector is created or deployed.
Open **Operations → Data Quality → Create Check**, discover both tables, confirm current-state
meaning, select rules, configure schedule/alerting, validate capabilities and save.
Run Now creates a durable Job, executed by the existing worker.

Verified directions are PostgreSQL → PostgreSQL, MySQL → MySQL and PostgreSQL →
MySQL. The reverse MySQL → PostgreSQL direction remains gated. Cross-provider
value comparisons do not establish schema equivalence: Schema Match certifies
integer domains, fails known narrowing/timezone-contract changes, and returns
INCONCLUSIVE for other unverified mappings.

## Six rules

| Rule | Condition | Options / defaults |
| --- | --- | --- |
| `row_count` | Actual COUNT equality | Full Table, optional filters/window |
| `row_count_diff` | Absolute or percentage COUNT difference | Nonnegative tolerance 0, BOTH directions; optional DESTINATION_BEHIND/AHEAD |
| `last_updated` | Absolute difference between MAX timestamps | EXACT or WITHIN_TOLERANCE (0 seconds), common timestamp semantics, explicit naive timezones, NULL policy |
| `freshness_delay` | Signed Source MAX minus Destination MAX | Maximum 300 seconds; source idle/activity policy; Destination ahead is INCONCLUSIVE |
| `data_age` | Evaluation time minus Destination MAX | Maximum 600 seconds; ONLY_WHEN_SOURCE_ACTIVE default or EXPECT_NEW_DATA; source activity window |
| `schema_match` | Current column definitions | Names/types/nullability on, order/PK off; EXACT/COMPATIBLE, mapping, exclusions, extra Destination allowed, missing Source columns disallowed |

Counts/MAX queries with identical scopes are shared within each endpoint session.
Both zero counts produce 0%; zero Source/nonzero Destination fails every finite
percentage threshold (`percentage_difference: null`, `relative_difference_infinite:
true`). Equal counts do not establish record equality.

Both temporal columns must mean the same thing. Business-update and ingestion
timestamps cannot be interchanged. Naive timestamps require an IANA timezone;
ambiguous/nonexistent DST times are INCONCLUSIVE. MySQL TIMESTAMP is read in UTC.
Future timestamps, clock skew, missing timestamps and inactivity are classified.
ONLY_WHEN_SOURCE_ACTIVE requires recent Source business updates and avoids false
CDC failure for idle workloads. Unknown activity is INCONCLUSIVE. EXPECT_NEW_DATA
enforces a continuous-arrival SLA even when Source is idle.

Schema types account conservatively for integer ranges/unsignedness, decimal
precision/scale, float width, string lengths/charset, date, timestamp timezone and
precision, boolean, binary and JSON. Safe widening may pass; narrowing fails.
Unknown types produce INCONCLUSIVE when type comparison is selected. Schema Match
does not apply filters or periods to the schema.

## Scope, schedules and confidence

Full Table never silently applies a lookback. Optional predicates are built from
discovered columns using equality/range/null operators and bound typed values;
arbitrary SQL is unavailable. Time Window requires timestamp mappings on both
sides. Rolling windows support 60 seconds to 31 days; fixed timezone-aware windows
are manual only. Boundaries persist in UTC:

```text
end = scheduled_evaluation_time - settling_seconds
start = end - lookback_seconds
window = [start, end)
```

Settling defaults to 120 seconds and does not establish a checkpoint. Activity
assessment uses the configured eligible scope. History displays times in the
configured display timezone; windows and that timezone remain in the run snapshot.
All evidence is **BEST_EFFORT**: independent observations may race with writes.
PASS means the configured condition passed; it does not certify CDC completeness.
There is no VERIFIED consistency option.

Execution and quality are separate: COMPLETED may contain FAIL. Overall precedence
is FAIL, ERROR, INCONCLUSIVE, then PASS only with complete valid coverage.
Skipped/unsupported coverage cannot produce a false PASS.

The frequency default is 600 seconds (10 minutes). Presets are 1/5/10/15/30 minutes
and 1/6/24 hours. Manual runs share the one-active-run guard but do not advance the
schedule. Pause prevents future occurrences; resume calculates a new due time.
Overlapping/missed occurrences are skipped rather than accumulated. Every run
retains its configuration version and immutable snapshot.

Selecting an enabled Slack/Telegram/Webhook channel activates alerting unless
explicitly disabled. No channel means no notifications. A later disabled/deleted
channel is shown unavailable while configuration is retained. Defaults: warning,
two consecutive conclusive FAILs, 900-second cooldown, recovery enabled. INCONCLUSIVE
does not increment counters or resolve incidents. ERROR has a separate incident
identity. PASS resets counters and resolves previously firing incidents. Existing
AlertManager, incidents, delivery queue and provider transports are reused.

## API

POST `/api/v1/data-quality/checks` with an authenticated session:

```json
{
  "name": "Orders current state",
  "source": {"connection_id": "00000000-0000-0000-0000-000000000001", "database": "commerce", "schema_name": "public", "table": "orders", "current_state_confirmed": true},
  "destination": {"connection_id": "00000000-0000-0000-0000-000000000002", "database": "analytics", "schema_name": "public", "table": "orders", "current_state_confirmed": true},
  "rules": [{"type": "row_count"}, {"type": "row_count_diff", "mode": "PERCENTAGE", "tolerance": 1}, {"type": "schema_match"}],
  "schedule": {"enabled": true, "frequency_seconds": 600}
}
```

IDs are placeholders; use authorized Connections. PATCH `checks/{id}` atomically
revalidates changes (nested sections are replaced). POST `checks/{id}/run` with
`{"request_key":"attempt-1"}` returns Run/Job IDs; repetition returns the same
retained run. `checks/{id}/runs` and `runs/{id}` expose paginated history/evidence.
`rules/catalog` publishes all six definitions and the complete configuration JSON
Schema; `validate` performs live capability checks. `connections`,
`connections/{id}/tables`, `columns` and `channels` expose safe discovery.

Admin has full access. Ops may read/create/edit/run/manage scheduling and
Check-specific alerts. Viewer reads safe checks/results/history/catalog only.
Deleting Checks requires Admin and retains histories. All endpoints enforce RBAC;
configuration and execution actions use existing audit records.

See [operations](../operations/data-quality.md),
[compatibility](../development/v0.3.4-data-quality-compatibility.md) and
[verification](../development/v0.3.4-data-quality-test-report.md).
