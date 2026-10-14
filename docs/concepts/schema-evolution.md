# Schema evolution

Discovery stores versioned source schemas and computes differences. Delivery validation maps supported source types to the target dialect and rejects ambiguous or unsafe shapes rather than guessing.

After adding a source column, run discovery again and save delivery mappings so ClueCDC refreshes its type metadata. With sink auto-evolution enabled and sufficient target permissions, compatible new columns can be added. ClueCDC pre-creates missing destination columns as nullable before updating the JDBC connector. This avoids connector failure when a source column is required or has a source-side default that Kafka Connect does not propagate as a destination default. Existing destination rows remain explicitly `NULL` until an operator starts an incremental resnapshot for that table.

Compatibility is classified conservatively:

| Source change | Classification | Operator action |
|---|---|---|
| Add nullable column | Non-breaking | Rediscover, refresh the delivery mapping, then resnapshot if old rows need values |
| Add required column with a default | Non-breaking | Rediscover and refresh; resnapshot to materialize the source default for old rows |
| Widen integer, decimal precision, or string length | Non-breaking | Verify destination capacity and migrate its type manually when needed |
| Change a column default | Potentially breaking | Review application semantics; destination defaults are not copied automatically |
| Remove a column | Breaking | Stop affected deliveries and perform an explicit destination migration |
| Narrow or incompatibly change a type | Breaking | Rebuild or manually migrate the affected destination table |
| Change a primary key | Breaking | Rebuild the delivery with the intended record key and reconcile data |
| Use an unsupported or ambiguous type | Rejected | Map it to a supported portable type before deployment |

Automatic evolution is additive only: ClueCDC does not automatically drop columns, rewrite destination types, change constraints, or change primary keys. Removing a table from capture does not drop destination data unless the operator explicitly selects that destructive behavior.

JDBC deliveries can enable automatic table evolution, but stable primary keys
remain essential for update and delete semantics. Snapshot and resnapshot operations are serialized per pipeline; a concurrent table removal is rejected, and a stopped snapshot records a cancelled terminal state rather than silently appearing successful.
