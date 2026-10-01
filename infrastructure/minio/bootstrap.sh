#!/bin/sh
set -eu
mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
mc mb --ignore-existing local/lakehouse
mc anonymous set none local/lakehouse
echo "MinIO bucket lakehouse is ready"
