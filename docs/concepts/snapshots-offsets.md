# Snapshots and offsets

Snapshot mode is selected when capture is created. `initial` snapshots existing rows when no prior offset exists; `no_data` begins with streaming only; `always` requests a new full snapshot when the connector starts.

Adding a table uses Debezium signaling for an incremental snapshot. Per-table resnapshot and stop-snapshot operations are exposed when the provider supports signals.

Kafka Connect owns source and sink offsets. ClueCDC observes them but does not edit them. Deleting a connector or topic can make replay and recovery impossible; retain source logs and Kafka topics for the longest supported outage and recovery window.

Connector, task, Connect worker, API and ClueCDC worker restarts retain the
existing connector name and Kafka Connect offsets. A restart is not an offset
reset and must not trigger a new initial snapshot when the offset state remains
available. The default JDBC upsert mode tolerates at-least-once replay for rows
with stable record keys; insert mode can fail or duplicate application effects.

ClueCDC deliberately exposes no generic **reset offsets** action. A missing WAL
segment, replication slot, binlog position or Debezium schema-history topic is
not repaired by silently recreating a connector. Restore the retained data-plane
state when possible. Otherwise, pause dependent deliveries, assess the replay
boundary, back up metadata, and create an explicit resnapshot/rebuild plan. This
can replay records and must be reconciled at every destination before service is
declared recovered.
