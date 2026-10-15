# Releases

ClueCDC follows Semantic Versioning. The `0.x` line may contain documented breaking changes in minor releases; patch releases should remain compatible.

Release preparation updates versions and the changelog, passes unit and live CDC checks, validates migrations and documentation, scans secrets/dependencies/images, and reviews third-party notices. Maintainers create a signed annotated tag and immutable container tags only from reviewed `main`.

The process is in `RELEASES.md`; the authoritative verification checklist is
[`docs/RELEASE_CHECKLIST.md`](../RELEASE_CHECKLIST.md). Pushing an approved
`v*` tag runs `.github/workflows/release.yml`; preparation never creates the tag.

The release workflow calls `hardening.yml` before publishing containers or
creating a release. This live gate includes security, integrity, recovery,
schema, metadata restore/upgrade, production container lifecycle and browser
checks in disposable fixtures. A manual Hardening acceptance run is available
before tagging.

On `release/v0.2.1-hardening`, `python scripts/release-check.py --preparation
--allow-dirty` runs the local checks plus the live core gate. For a fast edit
cycle only, add `--skip-live --skip-docker`; the output explicitly states that
live validation is incomplete. A final release still requires the full workflow
and target-environment rollout/network evidence from the checklist.
