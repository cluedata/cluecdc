# Releases

ClueCDC follows Semantic Versioning. The `0.x` line may contain documented breaking changes in minor releases; patch releases should remain compatible.

Release preparation updates versions and the changelog, passes unit and live CDC checks, validates migrations and documentation, scans secrets/dependencies, and reviews third-party notices. Container vulnerability review remains a checklist responsibility. Maintainers create a signed annotated tag only from the reviewed default branch.

The process is in `RELEASES.md`; the authoritative verification checklist is
[`docs/RELEASE_CHECKLIST.md`](../RELEASE_CHECKLIST.md). Pushing an approved
`v*` tag runs `.github/workflows/release.yml`; preparation never creates the tag.

## Candidate-first validation

Run `npm run release:plan` to inspect the package version, matching changelog,
release notes, pinned deployment images, SHA and local tag conflicts. It does
not fetch remote state or change files/tags. Use `python scripts/release.py plan
--json` for machine-readable output, or add `-- --require-new-tag` to the npm
command to fail on a local tag conflict.

The manual **Release candidate** workflow runs `release-validation.yml` and
`hardening.yml` on the selected branch. The tag-triggered publisher calls those
same workflows, avoiding a second quality definition. Shared validation invokes
`release-check.py --preparation --quality-only`; it also checks Kubernetes
schemas, scans Git history and archives source plus candidate metadata/checksums.

Hardening includes security, integrity, recovery, schema, metadata restore/upgrade,
production lifecycle and browser checks, plus isolated kind/Calico/HTTPS and
NetworkPolicy acceptance. Candidate runs have read-only repository permissions
and no publishing jobs. Review the exact SHA and all reports, not just a green
summary. Candidate success with draft metadata or an already-used version is
not permission to publish.

Publication requires finalized notes/date, default-branch ancestry and a
preflight that rejects existing releases/version images. The publisher does not
edit releases or clobber assets. Prereleases are marked explicitly and do not
advance stable aliases. Authentication failures block the preflight.

## Local live checks

Activate the prepared Python environment and install dependencies as described
in `RELEASES.md`. Set `WORKER_TEST_DATABASE_URL` to an isolated PostgreSQL test
database; the gate refuses to silently omit PostgreSQL coverage.

```bash
npm run release:quality -- --allow-dirty
python scripts/release-check.py --preparation --allow-dirty --browser --live-env-file <disposable-dotenv>
```

Preparation works on any named branch, not one version-specific branch.
The quality-only profile also works on a detached candidate checkout (as in tag
CI). The second command builds local images and runs the isolated live core and
browser gate; browser fixtures need an explicitly prepared disposable dotenv.
Never supply production credentials. Docker/Compose must be usable.

For a fast edit cycle only, `--skip-live --skip-docker` omits live gates and
release image builds; Compose configuration still needs the Docker CLI.
All skipping/dirty-tree flags require `--preparation`. Output identifies omitted
checks and never claims release approval. Local checks do not replace live
Kubernetes acceptance or target-environment rollout/network/operator review.

Do not move an existing tag to include a fix. Select an unused version after
maintainer review, rerun candidate checks, and approve that exact commit.
Partial publication requires manual investigation, not an automatic rebuild.

## Implementation references

Workflow reuse follows [GitHub's reusable workflow model](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).
Registry existence checks use the [Distribution manifest HEAD API](https://distribution.github.io/distribution/spec/api/#existing-manifests),
with [GHCR workflow authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
