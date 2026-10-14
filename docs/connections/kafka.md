# Kafka connections

Register the broker bootstrap list visible from the API and Connect network. The local value is `kafka:29092`; host tooling uses `localhost:9092`.

ClueCDC implements cluster/topic metadata, topic preparation and deletion, and
consumer-group inspection. CDC payload inspection requires direct Kafka or
destination access, never the API. It does not configure brokers, ACLs, quotas,
replication, or schema registry.

Supported client protocols are `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, and
`SASL_SSL`. SASL mechanisms are PLAIN, SCRAM-SHA-256, and SCRAM-SHA-512. TLS
uses platform trust by default and may add a private CA plus a PEM client
certificate/key. Hostname and certificate validation remain enabled; there is
no insecure skip-verify option. Python's SSL API loads an optional client key
through mode-0600 process-local temporary files and deletes them immediately
after constructing the SSL context.

Usernames, passwords, CA/certificate material, and private keys use the existing
encrypted metadata secret provider. APIs expose only configured/masked state.
Generated source producer, MySQL schema-history, and sink consumer settings use
ConfigProvider references, not plaintext. The ClueCDC Admin/consumer clients
decrypt credentials only for the bounded connection attempt.

Production rejects `PLAINTEXT` and `SASL_PLAINTEXT` registrations by default.
`ALLOW_INSECURE_KAFKA=true` is an explicit escape hatch for a reviewed private
network where transport encryption is supplied outside Kafka. Local Compose
continues to use PLAINTEXT for development.

Kafka Connect worker authentication is separate deployment configuration: its
worker and internal producer/consumer properties must match the broker. Kafka
Connect REST authentication is another independent boundary and is never
inferred from broker credentials. The Kustomize baseline demonstrates
SASL_SSL/SCRAM worker configuration through Kubernetes Secret references.

Unit tests verify PLAIN/SCRAM option mapping, TLS verification defaults,
invalid-CA rejection, encrypted storage, redacted APIs, and connector secret
references. Before production, run the live authenticated-cluster suite against
the exact broker CA, SASL mechanism, ACLs, and Connect image used by the target
environment.
