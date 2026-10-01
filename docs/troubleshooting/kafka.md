# Kafka errors

Verify broker reachability from both the API and Kafka Connect networks. Advertised listeners must return addresses those clients can resolve. Check authentication, authorization, topic existence, replication health, disk capacity, message size, retention, and consumer lag.

The local `kafka:29092` address works only inside Compose; host clients use the external listener. Topic deletion and retention expiry are not recoverable from ClueCDC metadata.
