# Snapshots and resnapshots

The initial snapshot policy is chosen at pipeline creation. Use a table resnapshot to rebuild a table's stream when Debezium signaling is enabled. ClueCDC records the asynchronous operation and its status; it cannot infer completion from seeing one snapshot record.

Resnapshotting re-emits rows and can affect destination load and update ordering. Check sink idempotence, primary keys, retention, and capacity before starting. The stop action asks Debezium to stop the active table snapshot; it does not roll back records already emitted.
