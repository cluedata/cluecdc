# Scaling

The stateless Next.js web tier can scale horizontally behind a Service. Kafka Connect should run multiple identical distributed workers; Connect coordinates connector tasks and rebalances them across workers.

The API serves HTTP only and can scale independently from `cluecdc-worker`.
Workers claim jobs using a short `FOR UPDATE SKIP LOCKED` transaction, commit
before external work, renew leases, and persist results in another transaction.
Expired leases recover; failed jobs use bounded backoff. SIGTERM/SIGINT cancel
in-flight operations and release their claims. Notification attempts also use
short leases and never retain row locks across provider requests.

`WORKER_CONCURRENCY` bounds active work (default 4). Reconciliation reads bounded
batches, closes metadata transactions before network calls, and then persists
observations. Observations may be repeated across replicas; external operations
and notification delivery have at-least-once semantics after a crash. Connect
connector names and job ownership protect normal concurrent requests, but an
ambiguous external timeout still needs operator inspection.

Apply schema migrations once per deployment before starting scaled API/worker
replicas. The local API command and Kubernetes init container are bootstrap
conveniences; coordinate migrations as a release step when scaling.

Scale Kafka and databases independently according to their own availability models. Connector `tasks.max` does not guarantee parallelism: source connector capabilities, partition count, and sink implementation determine actual task distribution.
