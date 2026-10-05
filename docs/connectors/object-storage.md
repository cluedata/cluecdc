# AWS S3 and MinIO destinations

Object-storage connections are destination-only. They deliver Kafka CDC topics
through the Apache-2.0 Aiven S3 sink, pinned to `3.4.2` with an artifact SHA256
check in the Connect image. They do not use JDBC, SQL mappings, schema creation,
envelope-flattening transforms or source database queries for transport.

## Connection

Register an `OBJECT_STORAGE` connection with provider `AWS_S3` or `MINIO` and
`DESTINATION` capability. Set bucket, region, optional prefix and access key ID /
secret access key; temporary credentials may include a session token. Credentials
are encrypted and omitted from normal API responses. Connect receives
`${cluecdc:...:field}` references, not plaintext stored connector credentials.
Rotation preserves the reference ID so deployed configs remain resolvable.
Restart/redeploy affected Connect tasks to pick up rotated credentials; automatic
live refresh is not guaranteed.

AWS S3 uses TLS and the regional endpoint. MinIO needs an explicit endpoint
reachable from API and Connect, usually `http://minio:9000` in the test stack.
Endpoint overrides require path-style access. Match `use_ssl` to the scheme;
TLS certificate verification cannot be disabled. Plain HTTP is intended only
for isolated local fixtures. Endpoint userinfo, queries and embedded credentials
are rejected.

Testing checks the bucket, writes a unique small probe under
`.cluecdc/connection-tests/`, checks it and deletes it in `finally`. The SDK runs
outside the API event loop. Testing never creates a bucket. Grant bucket
metadata/list and object put/get/delete permissions for the probe; delivery
also needs bucket checks and object writes. If deletion is denied, testing
fails and the unique probe may require manual removal. Provider errors and
validation echoes are sanitized.

## Delivery

Choose **Deliveries → Create object-storage delivery**, a capture, a storage
connection and its CDC topics. Preview checks the S3 plugin on the chosen
Connect cluster. Configure JSON Lines, gzip (default) or no compression,
records per file, flush interval, task count, key template and fail/continue
error policy. The default template partitions by topic/year/month/day and
includes partition and starting offset with `.jsonl.gz`. Uncompressed output
uses `.jsonl`. Preserve partition/offset tokens to avoid collisions. The
connection prefix scopes keys independently of the relative template.

Each line contains `key`, `value`, `offset` and `timestamp`. `value` preserves
the Debezium envelope: `op`, `before`, `after`, `source`, `ts_ms` and available
transaction metadata. Creates, updates, deletes, snapshots and null tombstones
are not converted to SQL upserts. This is raw CDC export, not a materialized
current-state database or table-format service.

Each delivery has an independent Connect consumer group and starts at the
earliest retained offsets. Kafka retention bounds replay. Delivery is at least
once; crashes/retries can replay records. Consumers should deduplicate using
topic/partition/offset information. File and flush settings trade latency
against object count; low-volume topics may wait for the flush interval.
Continue mode can skip bad records and must be an explicit operational choice.
No exactly-once export guarantee is claimed.

## Local acceptance

MinIO exists only in `compose.test.yaml`. Its image builds a pinned,
checksum-verified source release because former official image tags are
unavailable. It retains AGPLv3 and is an optional test fixture, not part of
ClueCDC runtime images. Bootstrap creates the bucket with fixture-only credentials.

```bash
docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait
python scripts/demo-object-storage.py
```

The test uses real PostgreSQL, Debezium, Kafka and the Aiven sink, then
decompresses MinIO objects to verify c/u/d keys, before/after, source metadata
and timestamps. CI needs no AWS account. Its sanitized report is
`artifacts/object-storage-verification.json`.

Upstream: [Aiven S3 connector](https://github.com/Aiven-Open/s3-connector) and
[pinned MinIO source](https://github.com/minio/minio/tree/RELEASE.2025-09-07T16-13-09Z).
