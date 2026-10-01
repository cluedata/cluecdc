# Releases

ClueCDC follows Semantic Versioning. The `0.x` line may contain documented breaking changes in minor releases; patch releases should remain compatible.

Release preparation updates versions and the changelog, passes unit and live CDC checks, validates migrations and documentation, scans secrets/dependencies/images, and reviews third-party notices. Maintainers create a signed annotated tag and immutable container tags only from reviewed `main`.

The authoritative checklist is `RELEASES.md` in the repository root.
