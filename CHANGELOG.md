# Changelog

All notable changes are recorded here. ClueCDC follows Semantic Versioning.

## [Unreleased]

### Added

- Open-source governance, security, support, contribution, and issue templates.
- Centralized configuration validation, provider extension seams, health aliases,
  structured request logging, and repository CI.
- Official Material for MkDocs site, GitHub Pages deployment, and Kubernetes baseline.

### Changed

- Local configuration and container builds are safer and reproducible.
- Repository links and publication metadata target the ClueData organization.
- Next.js and its ESLint configuration are updated to 16.3.8 to include the
  upstream `next/og` remote-code-execution fix.
- Database delivery operations use a consistent lock order so status polling
  cannot deadlock concurrent lifecycle requests.
- The pipeline wizard provisions topics before previewing delivery connector
  configuration.
- End-to-end checks follow the current UI, support alternate local ports, and
  clean up delivery resources before their parent pipelines.
- The compact navigation header no longer overflows narrow mobile viewports.

## [0.1.0]

Initial early-development release line. Earlier commit history is not presented
as a fabricated release history.
