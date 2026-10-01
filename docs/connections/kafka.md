# Kafka connections

Register the broker bootstrap list visible from the API and Connect network. The local value is `kafka:29092`; host tooling uses `localhost:9092`.

ClueCDC implements cluster/topic metadata, topic preparation and deletion, consumer-group inspection, and bounded event sampling. It does not configure brokers, ACLs, quotas, replication, or schema registry.

The bundled adapter is validated primarily with PLAINTEXT local Kafka. Treat production authentication modes as deployment work that must be integration-tested against your broker before use.
