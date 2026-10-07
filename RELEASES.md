# Release process

ClueCDC uses Semantic Versioning. During the `0.x` series, minor releases may
contain documented breaking changes; patch releases should remain compatible.

1. Update `CHANGELOG.md`, release notes, documentation, and the API package version.
2. Complete `docs/RELEASE_CHECKLIST.md` and run `python scripts/release-check.py`
   from a clean `main` checkout.
3. Confirm CI, live CDC verification, migrations, docs, secret/dependency scans,
   container builds, third-party notices, and generated checksums.
4. Create and push a signed annotated tag such as `v0.1.0` from the approved commit.
5. The tag workflow validates version consistency, publishes immutable `0.1.0`,
   `0.1`, and `latest` GHCR image tags, and creates the GitHub Release.
6. Verify image digests, GitHub Pages, release artifacts, and the released quick start.

Release tags and artifacts require maintainer approval. Never move a release tag
or rebuild an immutable version tag with different contents.
