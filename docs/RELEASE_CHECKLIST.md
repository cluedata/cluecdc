# Release checklist

Use this checklist from a reviewed, clean `main` checkout. Do not mark an item
complete unless it was verified against the release candidate commit.

## Code

- [ ] Main branch is clean
- [ ] CI passes
- [ ] Backend build passes
- [ ] Frontend build passes
- [ ] Tests pass
- [ ] Migrations pass from a fresh database and `alembic check` reports no drift

## Docker

- [ ] Web, API, and Connect images build
- [ ] Compose starts from a fresh clone and copied `.env.example`
- [ ] All health checks pass
- [ ] Persistent volumes survive a stop/start cycle

## CDC

- [ ] Source can be created
- [ ] Debezium connector runs
- [ ] Kafka receives events
- [ ] Delivery connector runs
- [ ] Destination receives INSERT
- [ ] Destination receives UPDATE
- [ ] Destination receives DELETE
- [ ] PostgreSQL to PostgreSQL passes
- [ ] PostgreSQL to MinIO passes
- [ ] Other supported paths are tested or explicitly recorded as not tested

## Documentation

- [ ] README is accurate
- [ ] Strict docs build passes
- [ ] Screenshots render
- [ ] Internal and external links work
- [ ] Changelog is updated

## Security

- [ ] No secrets are committed
- [ ] Runtime dependency scans pass and accepted development findings are reviewed
- [ ] Security policy and private reporting link are present
- [ ] Container and deployment defaults were reviewed

## Release

- [ ] Version is `0.1.0`
- [ ] Release notes are prepared
- [ ] Release workflow is ready
- [ ] `v0.1.0` Git tag has not been created during release preparation
- [ ] Maintainer has approved the exact commit to tag

## Post-release

- [ ] GitHub Release is visible
- [ ] `0.1.0`, `0.1`, and `latest` Docker image tags are available
- [ ] Published image labels and digests are correct
- [ ] Documentation is available
- [ ] Quick start is verified against released images/source
