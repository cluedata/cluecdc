# Apache Kafka

Kafka is the durable transport between capture and delivery. A pipeline's topic prefix and selected tables determine its capture topics. ClueCDC can prepare topics, inspect metadata, list consumer groups, and sample a bounded recent window without committing consumer offsets.

The included single KRaft broker is for development. Production needs an independently operated Kafka cluster with replication, authentication, authorization, encryption, retention planning, monitoring, and capacity management.

ClueCDC registers encrypted Kafka TLS/SASL client credentials and applies
compatible settings to its administration clients and managed connector
clients. It does not create broker identities, certificates, or ACLs. Kafka
Connect worker authentication and REST authentication are separate deployment
boundaries and must be configured independently.
