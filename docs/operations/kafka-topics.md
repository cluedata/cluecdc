# Kafka topics

ClueCDC lists broker-reported topics, partitions and offsets, not CDC payloads.
Inspect records directly with Kafka tooling or read destination objects. The
former payload-sampling API returns HTTP 410.

Topic deletion is intentionally privileged and destructive. A deleted capture topic can break active deliveries and remove the only retained replay history. Stop or detach dependent connectors, confirm ownership, and apply your Kafka backup/retention policy before deletion.
