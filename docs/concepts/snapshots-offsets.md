# Snapshots and offsets

Snapshot mode is selected when capture is created. `initial` snapshots existing rows when no prior offset exists; `no_data` begins with streaming only; `always` requests a new full snapshot when the connector starts.

Adding a table uses Debezium signaling for an incremental snapshot. Per-table resnapshot and stop-snapshot operations are exposed when the provider supports signals.

Kafka Connect owns source and sink offsets. ClueCDC observes them but does not edit them. Deleting a connector or topic can make replay and recovery impossible; retain source logs and Kafka topics for the longest supported outage and recovery window.
