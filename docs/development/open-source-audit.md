# Open-source audit

The publication workspace was produced from the current working tree without copying `.git`, `.env`, dependencies, caches, build outputs, test artifacts, or volumes. A fresh `main` repository avoids publishing personal commit metadata and any unreviewed historical objects.

Implemented code was verified for PostgreSQL/MySQL capture and JDBC delivery,
lifecycle operations, alerts, audit, structured logging, metrics,
local session authentication, invite onboarding, and Alembic migrations.
Providers without a
working source adapter, destination adapter, connector builder, and tests are
not exposed. Historical metrics are unavailable without a provider.

Apache License 2.0 is technically compatible with the repository's permissively licensed dependency model, but maintainers must confirm ownership and contributor permission for every included file before publication. Font license texts and a NOTICE file are retained. This audit is engineering evidence, not legal advice.

See `OPEN_SOURCE_READINESS.md` in the repository root for validation evidence and remaining publication actions.

## v0.1.0 release classification

The 2026-10-07 release pass classified and handled significant findings as follows:

| Priority | Finding                                                                                                            | Resolution                                                                                                                       |
| -------- | ------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| P0       | Pull requests and `main` ran documentation only, so normal CI did not validate the application                     | Restored quality, security, container, migration, E2E, and live CDC jobs for pull requests and branch pushes                     |
| P0       | No tag-driven workflow could publish reproducible artifacts, images, checksums, and a GitHub Release               | Added a least-privilege `v*` release workflow with version/tag validation and three component images                             |
| P0       | Production npm dependency tree included vulnerable `source-map-js` 1.2.1 and `sharp` 0.35.4                        | Updated the lockfile to compatible patched releases and made the production high-severity audit a CI gate                        |
| P1       | Release notes, checklist, changelog sections, and local release gate were incomplete                               | Added release-specific documents and non-publishing validation scripts                                                           |
| P1       | Component images lacked consistent OCI metadata                                                                    | Added source, version, revision, license, title, and description labels                                                          |
| P2       | Next.js ESLint transitively uses an unpatched development-only `braces` release                                    | Documented the upstream limitation; CI blocks critical findings in all dependencies and high findings in production dependencies |
| P2       | Production identity, TLS, secret management, Kafka ACL automation, HA, and backups are deployment responsibilities | Kept these as explicit `0.1.0` limitations rather than adding speculative infrastructure                                         |
