# Release process

ClueCDC uses Semantic Versioning. During the `0.x` series, minor releases may
contain documented breaking changes; patch releases should remain compatible.

## Prepare without a tag

1. Choose an unused version. `apps/api/pyproject.toml` is the version source;
   synchronize the matching `CHANGELOG.md` section, `docs/releases/v<VERSION>.md`,
   and all image references in `deploy/kubernetes/{api,worker,web,connect}.yaml`.
   Add the new release notes to `mkdocs.yml`; preserve historical notes.
2. Activate a Python 3.12+ virtual environment and install the gate dependencies:

   ```bash
   python -m pip install --upgrade pip==26.2.1
   python -m pip install -e "apps/api[dev]" -r requirements-docs.txt pip-audit==2.10.1
   npm ci
   npm run release:plan
   ```

   Node 24, Docker/Compose and `kubectl` must be available. Set
   `WORKER_TEST_DATABASE_URL` to a **disposable** PostgreSQL database; tests write
   to it. Never point it at application metadata. `release:plan` only reads local
   metadata/Git state: it does not fetch or establish remote availability.
3. For a fast local edit cycle, run `npm run release:quality -- --allow-dirty`.
   This uses the same quality command as release CI: metadata/deployment checks,
   Compose rendering, unit tests, application/docs builds, lint/types and
   dependency audits. It does not start live fixtures or grant release approval.
   See [local live checks](docs/development/releases.md#local-live-checks) for
   container/browser acceptance before committing.
4. Finalize release notes and the changelog date (`YYYY-MM-DD`), commit the
   candidate, and complete [the release checklist](docs/RELEASE_CHECKLIST.md).
   Run the manual **Release candidate** workflow on that branch, before creating
   any tag. It runs shared validation plus both live-hardening and Kubernetes
   gates with read-only repository permissions; it does not publish images.

   Maintainers can select the branch in Actions, or explicitly dispatch:

   ```bash
   gh workflow run release-candidate.yml --ref <candidate-branch>
   ```

   A newly added dispatch workflow must first be available on the default branch.
   Download `release-artifacts` (source archive, `release-metadata.json`,
   `SHA256SUMS`) and both hardening reports. Check the exact candidate SHA,
   warnings, test outcomes and cleanup. Draft/occupied-version candidates may
   be tested but are not promotable. Any later commit needs new evidence.

## Approve and publish

Only after reviewing the exact clean default-branch commit, target-environment
rollout evidence, dependency exceptions and third-party notices:

1. Fetch/review remote tags and verify the version is unused in GitHub/GHCR.
   `npm run release:plan -- --require-new-tag` also rejects an existing local tag.
   `python scripts/check-release-version.py --require-final` rejects draft notes
   and an undated changelog. Neither command creates a tag.
2. The maintainer separately creates and pushes a signed annotated
   `v<VERSION>` tag for the approved commit. There is intentionally no script or
   workflow that creates or pushes this tag for you.
3. The tag workflow re-runs the shared quality and both hardening gates on the
   tagged SHA; candidate artifacts are not blindly trusted for publication.
   A read-only preflight checks default-branch ancestry and rejects existing
   GitHub Releases or exact GHCR version tags. Authentication/network failures
   block publication, rather than being interpreted as absent images.
4. Publish `ghcr.io/<owner>/cluecdc-{web,api,connect}:<VERSION>` and create the
   GitHub Release with source/checksum/metadata assets. Stable releases also move
   the `<MAJOR>.<MINOR>` and `latest` aliases; prereleases do not move them.
   Workers use the API image. Verify published digests/labels, docs and the
   released quick start. Exact versions are immutable; aliases are not.

Release tags and artifacts require maintainer approval. Never move a release tag
or rebuild an immutable version tag with different contents.

If validation fails after tagging, fix the code and select a new version/tag;
do not retarget the old tag. If publication partially succeeds, preserve the
published images/assets and investigate manually. The preflight intentionally
blocks automatic retries that would overwrite an existing version. Package
access must be granted to the workflow; a registry authorization failure is not
proof that a tag is free. The preflight is not a registry-wide lock: restrict
other publishers to avoid races.
