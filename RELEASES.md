# Release process

ClueCDC uses Semantic Versioning. During the `0.x` series, minor releases may
contain documented breaking changes; patch releases should remain compatible.

1. Update `CHANGELOG.md`, documentation, and the API/package version.
2. Run CI, live Compose CDC verification, migration upgrade/check, docs, secret,
   dependency, and container scans.
3. Review third-party notices and generated SBOMs without committing build output.
4. Create a signed annotated tag such as `v0.2.0` from reviewed `main`.
5. Publish immutable container tags and digests, then create GitHub release notes.
6. Verify the GitHub Pages deployment and perform a clean-install smoke test.

Release tags and artifacts require maintainer approval. Never rebuild an existing
version tag with different contents.
