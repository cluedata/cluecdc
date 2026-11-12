# Release checklist

Use a reviewed, clean default-branch checkout and record the exact candidate SHA.
Automatic CI selects changed components; release validation explicitly runs API
tests. Record optional
manual checks separately; an API PASS does not establish UI or real CDC behavior.

## Required API and release checks

- [ ] Candidate commit is reviewed and the working tree is clean
- [ ] **CI checks** passes for the selected components; release API tests include PostgreSQL worker coverage
- [ ] `release:plan` matches package/lockfile, changelog, release notes and deployment images
- [ ] Release notes are finalized and the changelog date is `YYYY-MM-DD`
- [ ] `check-release-version.py --require-final` passes
- [ ] `release.py plan --require-final` passes with all required acceptance gates and a clean checkout
- [ ] Advisory limitations are recorded in the release notes; v0.3.5 G13 real HTTPS delivery/cooldown remains unverified
- [ ] Release candidate workflow passes for the exact approved commit
- [ ] Candidate source archive, metadata, checksums and API test report are reviewed
- [ ] Version is unused locally, on GitHub and in GHCR; no existing tag is moved
- [ ] Release workflow and GitHub/GHCR package access are ready
- [ ] Maintainer approves the exact commit before creating its signed release tag

## Optional manual validation

Select these workflows explicitly when relevant to a deployment. They are not
automatically executed by API CI or publication. Outcomes declared as mandatory
in the release acceptance configuration still block publication until PASS.

- [ ] Extended validation: frontend tests/build, lint/types, dependency/secret scans,
      Compose rendering and native image/runtime checks
- [ ] Data Quality: real PostgreSQL/MySQL rules, migration round trip and browser cases
- [ ] CDC reliability: CRUD, replay, outages, snapshots and source-history diagnostics
- [ ] Experimental source CDC fixtures, with unavailable infrastructure marked NOT RUN
- [ ] Hardening: metadata upgrade/restore, secret boundaries, Kafka TLS/SASL and browser flows
- [ ] Kubernetes/target-cluster rollout, HTTPS and NetworkPolicy/CNI acceptance
- [ ] Container vulnerability review and third-party notices
- [ ] Documentation build and GitHub Pages publication

## Post-release

- [ ] GitHub Release is visible with source/checksum/metadata assets
- [ ] Exact web, API/worker and Connect image versions are available
- [ ] Published digests and version/revision labels match the tagged commit
- [ ] Stable releases advance minor/latest aliases; prereleases do not
- [ ] Quick start and selected deployment are checked against published artifacts
