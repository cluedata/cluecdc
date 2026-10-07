# Releases

ClueCDC follows Semantic Versioning. The `0.x` line may contain documented breaking changes in minor releases; patch releases should remain compatible.

Release preparation updates versions and the changelog, passes unit and live CDC checks, validates migrations and documentation, scans secrets/dependencies/images, and reviews third-party notices. Maintainers create a signed annotated tag and immutable container tags only from reviewed `main`.

The process is in `RELEASES.md`; the authoritative verification checklist is
[`docs/RELEASE_CHECKLIST.md`](../RELEASE_CHECKLIST.md). Pushing an approved
`v*` tag runs `.github/workflows/release.yml`; preparation never creates the tag.
