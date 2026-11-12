# Releases

The canonical procedure is [RELEASES.md](https://github.com/cluedata/cluecdc/blob/main/RELEASES.md)
and the [release checklist](../RELEASE_CHECKLIST.md).

## One validation path

Both the manual candidate and tag publisher call `release-validation.yml`.
It has one validation job, with a disposable PostgreSQL service, instead of
calling the general CI selector and creating unrelated skipped component jobs.

```text
Candidate: validate → source/metadata/checksum artifacts (no publication)
Tag:       validate → images (web, API/worker, Connect) → GitHub Release
```

Validation checks metadata and configured mandatory acceptance first. Publication
also checks default-branch ancestry and authenticated GitHub/GHCR availability
before installing test dependencies. API tests then run once and source artifacts
are generated only after they pass. The service container initializes before
steps; invalid metadata does not install dependencies or run tests/builds.

```bash
python scripts/release.py plan
python scripts/release.py plan --require-final
```

The first command is preparation: it reports drafts, blocked acceptance, dirty
state and tag conflicts. The second is the publication gate: metadata must be
final, required gates must all PASS and the checkout must be clean. Neither
command fetches, edits files, creates tags or publishes. Add `--json` for structured
results, including a BLOCKED error report when validation fails.

`check-release-version.py --require-final` checks only version metadata. A dated
changelog and prepared notes can pass while G13 remains BLOCKED. Publication
enforces required acceptance, including during per-image retry preflight. For
v0.3.5 the maintainer explicitly made G13 advisory: its actual BLOCKED status is
reported in metadata, CLI output and the workflow summary without stopping release.
Matrix records must contain each required and advisory gate exactly once; missing,
duplicated or unknown records fail validation. Required non-PASS results cannot
publish. PASS records require evidence references.
A record checker does not replace review of actual receipts/artifacts or final-SHA
coverage. The current 0.3.5 notification gate is not marked PASS by this refactor.

## Evidence and permissions

Candidate validation is read-only and has no publishing jobs. Download `api-tests`
and `release-artifacts` for the exact SHA. Metadata reports acceptance and warnings;
a green candidate run is not publication approval. Live CDC, real Data Quality,
browser and Kubernetes suites remain manual, with unavailable scenarios reported
separately. Required acceptance evidence is not made optional by leaving those
heavy suites outside the default workflow.

The publisher adds package read permission only to validation, package write only
to image jobs and repository write only to Release creation. Called workflows
inherit caller permissions rather than elevating them. This follows
[GitHub's reusable-workflow permission model](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).

Existing immutable-release protections remain: remote errors fail closed,
per-image attempts recheck availability, downloaded assets must match the tag/SHA
and checksums, and publication does not overwrite existing Releases or assets.
The signed-tag/maintainer approval step remains outside automation.

## Local extended checks

Use a disposable PostgreSQL database for `WORKER_TEST_DATABASE_URL`, never the
application database. Optional broad checks remain available:

```bash
npm run release:quality -- --allow-dirty
python scripts/release-check.py --preparation --allow-dirty --browser --live-env-file <disposable-dotenv>
```

Skipping checks requires preparation mode and never grants release approval.
Do not modify a running installation to validate the release. The existing manual
hardening, CDC, Data Quality and experimental-provider workflows remain available.
