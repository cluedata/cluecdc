# Security policy

## Supported versions

ClueCDC is currently in the `0.x` development series. Security fixes are made
for the latest released minor version only. Deployments should track the newest
patch release and review the changelog before upgrading.

## Reporting a vulnerability

Do not report vulnerabilities in public GitHub issues, discussions, logs, or
pull requests. Use [GitHub private vulnerability reporting](https://github.com/cluedata/cluecdc/security/advisories/new).
Maintainers must enable that feature before announcing the repository; if the
link is unavailable, contact a ClueData organization owner privately and do not
post vulnerability details publicly.

Include affected versions, impact, reproduction steps, and a minimal proof of
concept. Remove credentials, customer data, database/table names, topic payloads,
and other sensitive data. Maintainers should acknowledge a report privately,
agree on disclosure timing, and credit the reporter if requested.

## Deployment guidance

The supplied Compose stack is for loopback-only development, not an internet-
facing production deployment. Production operators must:

- use local session authentication with a unique `SESSION_SECRET` and unique
  encryption/service/database secrets;
- bootstrap a named Admin, disable departed users promptly, and assign Ops or
  Viewer unless administrative access is required;
- terminate TLS and keep API, Connect, brokers, and databases on private networks;
- enable Kafka authentication/ACLs and secure Kafka Connect separately;
- restrict source/destination roles to required databases and tables;
- protect and back up the metadata database together with its encryption key;
- rotate credentials through a coordinated connector update;
- restrict `/metrics`, API docs, and internal secret resolution endpoints;
- review container images, dependencies, retention, audit logs, and backups.

ClueCDC application telemetry is disabled by default and the Community Edition
does not require a ClueCDC-hosted service.
