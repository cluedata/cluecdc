# Kafka topics

ClueCDC lists broker-reported topics, partitions, offsets, and bounded recent event samples. Sampling uses no consumer group, commits no offsets, and is constrained by record count, partitions, bytes, and time.

Topic deletion is intentionally privileged and destructive. A deleted capture topic can break active deliveries and remove the only retained replay history. Stop or detach dependent connectors, confirm ownership, and apply your Kafka backup/retention policy before deletion.
