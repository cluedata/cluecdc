# Change Data Capture

Change Data Capture records committed row changes without polling whole tables. PostgreSQL exposes logical WAL; MySQL exposes row-based binlogs. Debezium converts those database records into ordered Kafka records containing keys, operation type, source metadata, and row values.

ClueCDC manages the desired connector configuration and observes runtime state. It does not replace database logs, Debezium offsets, Kafka durability, or sink semantics. End-to-end recovery therefore depends on source log retention, Kafka retention, connector offsets, stable record keys, and destination idempotence.

Initial snapshots establish a starting dataset before streaming continues. An individual `READ` event proves one snapshot row, not completion of the whole snapshot.
