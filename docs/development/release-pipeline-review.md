# Release pipeline review

Prepared on 2026-11-11; policy updated on 2026-11-12 after the maintainer requested
removing the G13 publication blocker. The refactor was merged through PR #21.
No tag or publication is changed by this follow-up.

## G13 advisory policy update

The release declaration now requires 17 gates and lists G13 under
`advisory_acceptance_gates`. Strict planning and all publication preflights enforce
the required gates. The original G13 BLOCKED record and evidence remain unchanged:
real external HTTPS delivery/cooldown has not been verified. CLI output, metadata,
the workflow summary and release notes retain that limitation without stopping
publication. Required failures and malformed/missing advisory records still fail.
SSRF restrictions and remote publication protections are unchanged.

Follow-up validation: 240 release/CI tests passed, including blocked advisory
preflight, unchanged evidence and required failures remaining blocking. Ruff,
actionlint, repository safety, offline lock validation and strict docs also passed.
These are local checks, not a claim of successful remote publication.

The following findings and validation results describe the original refactor,
before this explicit policy update.

## Findings and changes

| Finding | Effect before refactor | Change |
| --- | --- | --- |
| Metadata was checked after backend tests | Invalid date/draft notes consumed an entire API job before failing | Metadata and configured acceptance are checked before dependency installation and tests |
| Release validation called the generic CI selector | Nested workflow graph contained unrelated skipped jobs and an extra required-check wrapper | One shared validation job runs API tests directly against disposable PostgreSQL |
| Metadata was parsed repeatedly to validate and build the plan | Separate checks and repeated plan calls could report different stages without one source descriptor | One `release.py plan` invocation produces the exact metadata/acceptance/SHA artifact and summary |
| G13 was a manual report blocker, not a publication check | Finalizing text alone could allow publication despite missing real notification evidence | Required gate IDs/matrix are declared in package metadata and enforced by strict planning and every publication preflight |
| The package lock version was not checked | A package version correction could leave the editable lock entry stale | Version validation also checks the unique `cluecdc-api` entry in `uv.lock` |
| A legitimate current tag generated an occupied-version warning | Tag-triggered validation warned about the tag it was supposed to validate | The current SHA's tag is expected; a tag pointing elsewhere remains a warning and `--require-new-tag` still rejects occupied tags |

## Resulting flow

```text
Candidate: one validate job → source + metadata + checksum + API test artifacts
Publisher: validate → three image jobs → GitHub Release
```

The metadata step checks package/lockfile, expected tag, changelog, notes and image
pins. In publication mode it also requires every configured acceptance record to
PASS and the checkout to be clean. The read-only default-branch/remote-availability
preflight follows, then dependency installation, API/native PostgreSQL tests and
source-archive generation. The PostgreSQL service initializes before job steps;
fail-fast claims concern dependency installation/tests/builds, not service startup.

Candidate mode allows draft or blocked records for testing, and displays their
actual state. A successful candidate is not publication approval. Publisher mode
rejects every non-PASS state. Missing/duplicate/unexpected gate IDs, invalid status
values, missing evidence references and invalid/missing configured matrices fail
closed. Evidence references must still be reviewed against actual receipts and
the final source; this parser does not certify that a referenced test ran.

The declaration lives under `[tool.cluecdc.release]`. It remains configured across
version bumps; gates are not silently removed by choosing another version.
Historical releases without a declaration retain the metadata validator's legacy
behavior. The original declaration required all G01–G18; the update above makes
G13 advisory without changing any recorded result.

## Metadata finalization versus publication

The changelog has the preparation date `2026-11-11` and notes no longer carry the
draft marker. This resolves the reported metadata error:

```text
python scripts/check-release-version.py --tag v0.3.5 --require-final
Release metadata is consistent for v0.3.5.
```

The complete publication command reports the real outstanding requirement:

```text
python scripts/release.py plan --require-final
Mandatory release acceptance is incomplete:
- G13: BLOCKED - real HTTPS notification delivery/cooldown evidence is missing
```

`--json` also writes a structured BLOCKED record on failure. Neither command edits
files, resets data, changes tags or publishes. The compatibility metadata command
remains available; all publication entry points enforce acceptance independently.

## Preserved protections

- API test execution remains mandatory, with a dedicated PostgreSQL 17.9 service
  for concurrency and legacy migration coverage. Live DQ/CDC suites remain manual;
  skipped tests are not counted as passes and required evidence still gates release.
- The publisher requires finalized metadata, required acceptance and default-branch
  ancestry before installing dependencies. Candidate gets contents-read only;
  publisher validation additionally gets packages-read. Write permissions remain
  confined to image publication and GitHub Release creation.
- Remote authentication/network errors remain blocking. Every image attempt still
  rechecks its own immutable version, including partial-publication retries.
- Source archives are generated only after API tests succeed. Downloaded artifacts
  still require checksums, exact tag/SHA and clean-source identity before release.
- No release overwrite/clobber flag, automatic tag creation, unsigned-tag fallback,
  default-branch force push, SSRF exception or automatic acceptance waiver was added.

## Validation

Focused release/CI contracts: **221 passed**. These cover lock drift, acceptance
states and malformed/missing evidence, strict JSON errors, clean-source enforcement,
the current-tag case, job ordering, read/write permissions, PostgreSQL coverage,
artifact identity and retry protections. Actionlint passes for all three workflows.
Full backend regression: **790 passed, 93 skipped, zero failed**, with one existing
pytest warning. The skips are 86 live Data Quality cases plus seven PostgreSQL
cases because Docker is unavailable locally. They are not counted as passes;
the new workflow still supplies PostgreSQL and requires those native tests.
Focused totals overlap the full backend total and must not be added together.

Ruff check/format, mypy, repository safety, deployment rendering, actionlint,
`uv lock --check --offline` and strict docs all passed. The guard checks separately
confirm that metadata finalization exits 0 while strict planning and publication
preflight exit 1 with G13 BLOCKED. Expected refusal is evidence that the gate
works, not evidence of release readiness.

Logs, JUnit and command outcomes are under
`artifacts/release-v0.3.5/pipeline-refactor/`. No GitHub run of this unmerged
refactor or performance benchmark is claimed.

The existing v0.3.5 tag points to the earlier commit `35d888e`; rerunning that
tag's old workflow cannot pick up these source changes. Merge/revalidate the new
candidate before any separately authorized tag/publication action. The previous
retag's evidence remains under `artifacts/release-v0.3.5/authorized-retag/`.
