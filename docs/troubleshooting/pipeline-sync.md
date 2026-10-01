# Pipeline synchronization issues

Trace the record boundary by boundary:

1. Confirm the source transaction committed and the table is in the connector filter.
2. Confirm the capture task is running and its offset advances.
3. Inspect the expected Kafka topic and key/value envelope.
4. Confirm the delivery task is running and consuming the correct topic.
5. Check target mapping, key fields, supported types, permissions, and constraints.

After a source schema change, rediscover, resave mappings, and restart the failed sink task. A new delivery starts from the earliest retained offset; it cannot replay records already expired from Kafka.
