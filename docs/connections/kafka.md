# Kafka connections

Register the broker bootstrap list visible from the API and Connect network. The local value is `kafka:29092`; host tooling uses `localhost:9092`.

ClueCDC implements cluster/topic metadata, topic preparation and deletion, and
consumer-group inspection. CDC payload inspection requires direct Kafka or
destination access, never the API. It does not configure brokers, ACLs, quotas,
replication, or schema registry.

The bundled adapter is validated primarily with PLAINTEXT local Kafka. Treat production authentication modes as deployment work that must be integration-tested against your broker before use.
