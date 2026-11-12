# Release process

ClueCDC uses Semantic Versioning. `apps/api/pyproject.toml` is the version source.
The pipeline is **validate → images → GitHub Release**. Preparation and candidate
validation never create tags or publish artifacts to GHCR.

## 1. Prepare and validate

Synchronize the package and `apps/api/uv.lock`, the version's changelog section,
`docs/releases/v<VERSION>.md`, documentation navigation and all four Kubernetes
image manifests. Workers use the API image. Finalize the changelog date as
`YYYY-MM-DD` and remove the notes' `Unreleased draft` marker when their content is
prepared. Metadata finalization does not mean acceptance or publication approval.

Use one read-only command during preparation:

```bash
python scripts/release.py plan
```

The plan reports metadata, configured acceptance gates, exact SHA, dirty state,
local tag conflicts and warnings. `--json` produces machine-readable evidence.
The publication check is:

```bash
python scripts/release.py plan --require-final
```

This rejects draft metadata, blocked/missing configured acceptance gates and an
uncommitted checkout. Add `--require-new-tag` before creating a new version's tag.
`check-release-version.py --require-final` remains a metadata-only compatibility
command; it is not the complete publication gate.

Mandatory acceptance is declared under `[tool.cluecdc.release]` in the package
metadata. A configured matrix must include every required gate exactly once, with
valid statuses and evidence references for PASS records. Every required non-PASS
state blocks publication. Explicitly declared `advisory_acceptance_gates` remain
visible in metadata and summaries without blocking publication. For v0.3.5, G13
is advisory at the maintainer's request: real HTTPS notification delivery/cooldown
remains unverified. Keep that limitation in the release notes; never mark the
record PASS without actual execution evidence. Bumping the version does not
remove this declaration.
The checker validates records; maintainers still review the evidence and its
relationship to the final candidate SHA.

Commit/review the candidate through the normal pull request process. Then run the
read-only **Release candidate** workflow on that exact branch/ref:

```bash
gh workflow run release-candidate.yml --ref <candidate-branch>
```

It uses the same single validation job as publication, permits draft/blocked
candidates for testing, runs API tests against disposable PostgreSQL, and produces
`api-tests` plus `release-artifacts` (source archive, `release-metadata.json`,
`SHA256SUMS`). Its summary explicitly reports acceptance. A green candidate job
with blocked acceptance is not promotable. Any later commit needs fresh evidence.

Live CDC, browser, Data Quality and deployment suites remain manual. They are not
silently counted as PASS or automatically rerun by release CI. Their configured
mandatory acceptance outcomes still block publication until supported by evidence.
For broad local validation, set `WORKER_TEST_DATABASE_URL` to a disposable database
and use `npm run release:quality -- --allow-dirty`. Never use application metadata.

## 2. Approve and tag

Review the clean default-branch SHA, candidate artifacts/checksums, API results,
configured acceptance and all warnings using [the checklist](docs/RELEASE_CHECKLIST.md).
Verify signing and separately obtain maintainer approval for that exact commit.
Verify remote tag, GitHub Release and authenticated GHCR version availability;
authentication or network failures are not proof of absence.

The maintainer creates and pushes a signed annotated `v<VERSION>` tag. No script or
workflow does this automatically. Existing release tags and exact images are
immutable under the normal policy. Never force-push the default branch or bypass
its protection. A previous explicitly authorized retag exception does not grant
permission to retarget future releases or overwrite published assets.

## 3. Publish and verify

The tag workflow has three stages:

1. **Validate:** one shared job checks version/lockfile/notes/image pins and all
   configured acceptance before installing API dependencies. It also checks
   default-branch ancestry and authenticated remote availability before tests.
   API tests use disposable PostgreSQL; only then are source/checksum artifacts built.
2. **Images:** build/push web, API/worker and Connect for AMD64/ARM64. Every attempt
   rechecks its own immutable version immediately before publication.
3. **GitHub Release:** verify downloaded checksums and exact tag/SHA identity,
   then create the Release without editing or clobbering existing assets.

The shared workflow inherits bounded caller permissions: candidate gets repository
read access; publication validation additionally gets package read access. Only
image jobs receive package write access, and only Release creation receives
repository write access. Metadata and acceptance failures stop before dependency
installation, tests and image builds (the job's PostgreSQL service initializes first).

Stable publication advances minor/latest aliases; prereleases do not. Verify the
Release, source/checksums/metadata, image digests, AMD64/ARM64 manifests and version/
revision labels before reporting RELEASED. Workers continue using the API image.

If publication partially succeeds, preserve the immutable assets and investigate.
Retries intentionally refuse occupied versions. Do not blindly rerun publication,
move tags or rebuild existing exact image tags. The preflight is not a registry-wide
lock; restrict concurrent publishers. No release verification deploys to an existing
installation.
