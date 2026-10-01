# Apache Kafka

Kafka is the durable transport between capture and delivery. A pipeline's topic prefix and selected tables determine its capture topics. ClueCDC can prepare topics, inspect metadata, list consumer groups, and sample a bounded recent window without committing consumer offsets.

The included single KRaft broker is for development. Production needs an independently operated Kafka cluster with replication, authentication, authorization, encryption, retention planning, monitoring, and capacity management.

ClueCDC does not currently provision SASL credentials or ACLs. Kafka security settings registered with the control plane must match the capabilities implemented by its adapter.
