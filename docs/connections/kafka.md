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
inferred from broker credentials. The unreferenced development Connect manifest
demonstrates SASL_SSL/SCRAM worker configuration through Kubernetes Secret
references; the production base uses independently operated Connect.

For the pinned Debezium image, worker properties use `CONNECT_SECURITY_PROTOCOL`,
`CONNECT_SASL_MECHANISM`, and `CONNECT_SASL_JAAS_CONFIG`, with equivalent
`CONNECT_PRODUCER_*` and `CONNECT_CONSUMER_*` properties. Unprefixed security
variables are ignored by its entrypoint. Set `CONNECT_SSL_TRUSTSTORE_TYPE=PEM`
(also for producer/consumer) and provision CA material through protected files.
The entrypoint parses environment values line by line. For workers hosting
ClueCDC connectors, use inline PEM via Kafka's built-in `FileConfigProvider`:
enable `CONNECT_CONFIG_PROVIDERS=cluecdc,file`, configure
`CONNECT_CONFIG_PROVIDERS_FILE_CLASS=org.apache.kafka.common.config.provider.FileConfigProvider`,
and set `CONNECT_SSL_TRUSTSTORE_CERTIFICATES=${file:/path/ca.properties:ca}`
(also for producer/consumer). Mount `ca.properties` through a Secret whose
`ca` property contains the PEM with Java-properties escaped newlines (`\n`).
Do not also set a worker truststore location. Kafka rejects inherited file
locations combined with a connector's inline PEM, and Connect rejects null
overrides that would clear them. Use the same inline approach for a worker
keystore when connector-level client certificates are required. Hostname and
certificate verification remain enabled. The reference manifest expects the
Secret key `kafka-ca-properties` for this properties file.

Unit tests verify PLAIN/SCRAM option mapping, TLS verification defaults,
invalid-CA rejection, encrypted storage, redacted APIs, and connector secret
references. Before production, run the live authenticated-cluster suite against
the exact broker CA, SASL mechanism, ACLs, and Connect image used by the target
environment. `scripts/kafka_security_acceptance.py` creates short-lived TLS/mTLS
and SASL fixtures in an isolated integration network, checks both Python Kafka
clients and negative authentication/trust cases, and runs the four JDBC CDC
paths through a separate SCRAM-SHA-512/SASL_SSL Connect cluster. Fixture keys and
credentials are temporary and excluded from report artifacts.
