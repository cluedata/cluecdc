# S3-compatible object storage

Supported providers are Amazon S3 and MinIO.
They share one provider implementation and support endpoint, region, bucket,
base path, access/secret/session credentials, path-style addressing, TLS, a
custom CA, and an optional write check.

`POST /api/v1/connections/{id}/test` performs bucket metadata access, a bounded
prefix listing, and (by default) put/head/delete of a unique temporary object.
Cleanup runs in `finally`; no test object should remain. The response contains
individual endpoint, authentication, bucket, prefix, and write diagnostics.

AWS S3 may omit the endpoint and use workload credentials. MinIO connections
require an endpoint and default to path-style access.
Credentials are encrypted and API responses contain only a configured flag and
mask.
