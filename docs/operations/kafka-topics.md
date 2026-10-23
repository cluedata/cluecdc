# Kafka topics

ClueCDC lists broker-reported topics, partitions and offsets. For topics owned
by active pipelines, the **Messages** tab and **CDC events** workspace can read
an explicit bounded sample of recent records without committing offsets. Use
Kafka tooling or destination objects for full history, search, and export.

Topic deletion is intentionally privileged and destructive. A deleted capture topic can break active deliveries and remove the only retained replay history. Stop or detach dependent connectors, confirm ownership, and apply your Kafka backup/retention policy before deletion.
