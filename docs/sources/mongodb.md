# MongoDB source setup

Use MongoDB 6.x, 7.x or 8.x with a replica set. Standalone deployments are rejected
for CDC. Sharded clusters are deliberately unverified and blocked by topology
readiness. Atlas-compatible standard/SRV URIs use the same MongoDB source type.

Enter the entire connection URI in the password-style **MongoDB URI** field.
It is stored as the encrypted `connection_uri` secret, not endpoint config JSON,
API output or audit payloads. Include username/password, `authSource`, replicaSet
and read preference in the URI. Percent-encode reserved credential characters.
The API accepts primary or primaryPreferred read preference and rejects insecure
TLS verification options. Certificate verification is enabled by default.

Example payload (replace credentials securely; do not put real URIs into shell
history or application logs):

```json
{
  "name": "Documents",
  "provider": "MONGODB",
  "category": "DATABASE",
  "capabilities": ["SOURCE"],
  "config": {"database_name": "inventory", "ssl_enabled": true},
  "credentials": {"connection_uri": "mongodb+srv://user:replace@cluster.example.net/?authSource=admin"}
}
```

The deprecated password-based `/sources` creation DTO is not the MongoDB URI
entry point; use `/connections`. Existing source detail/discovery/pipeline APIs
remain available for saved MongoDB source IDs.

Grant find, changeStream and listCollections on the captured database. Database
listing needs listDatabases as appropriate. Reading the oplog window requires
additional local database access and is often restricted on Atlas; missing
visibility is **Unknown**, not a zero retention guarantee. Readiness opens and
closes a real read-only change stream without changing the collection.

For local fixture use only, the acceptance runner explicitly initializes a
single-member `cluecdc-rs`. Production replica sets need a topology appropriate
to availability requirements. The fixture has no authentication and disables
TLS; it does not validate Atlas authentication, trusted TLS or replica failover.

Discovery lists document collections, excluding views/system collections. `_id`
is always the document identity. ObjectId, Decimal128, dates, binary, nested
objects, arrays, booleans and nulls remain BSON extended JSON inside the raw
Debezium envelope. No sampled relational schema or flattening is introduced.

`capture.mode=change_streams` preserves partial updates and updateDescription.
Snapshot and insert events contain extended-JSON document text. Update patches,
document replacement and delete envelopes remain distinguishable through their
original Debezium fields; an absent `after` is never an incomplete upsert.
Only S3/MinIO event-log delivery is enabled. Stateful delivery, custom identities
and online collection changes/resnapshot are blocked.

Debezium owns resume tokens and durable offsets. Temporary outages can recover
using existing task restart; expired tokens require `SOURCE_RECOVERY_REQUIRED`.
Do not fabricate a token, reset offsets or recreate a connector. Restore history
or approve an isolated snapshot with an explicit destination reconciliation plan.

See [upstream MongoDB event and permission documentation](https://debezium.io/documentation/reference/3.3/connectors/mongodb.html)
and [local evidence](../sources-extension-report.md).
