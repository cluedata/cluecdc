# Scaling

The stateless Next.js web tier can scale horizontally behind a Service. Kafka Connect should run multiple identical distributed workers; Connect coordinates connector tasks and rebalances them across workers.

The API includes an in-process reconciliation and alert-delivery worker. Until leader election or a separately deployable worker is implemented, run one worker-enabled API replica. Read-only API scaling would require separate API and worker process settings not exposed by the current Compose preset.

Scale Kafka and databases independently according to their own availability models. Connector `tasks.max` does not guarantee parallelism: source connector capabilities, partition count, and sink implementation determine actual task distribution.
