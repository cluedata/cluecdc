# MinIO is a test fixture only. Its former public registry images are unavailable.
# Build the pinned AGPL-3.0 upstream release, keeping its source and license accessible.
FROM golang:1.24.6-bookworm AS builder
ADD --checksum=sha256:c9598dcce3440977e79f787f2ba0e7e4d92c8d556bd51e7cef3785bafd6635f3 https://github.com/minio/minio/archive/refs/tags/RELEASE.2025-09-07T16-13-09Z.tar.gz /tmp/minio.tar.gz
RUN mkdir /src && tar -xzf /tmp/minio.tar.gz -C /src --strip-components=1
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /minio .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install --no-install-recommends -y ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home minio && mkdir /data && chown minio:minio /data
COPY --from=builder /minio /usr/local/bin/minio
COPY --from=builder /src/LICENSE /usr/share/licenses/minio/LICENSE
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.version="RELEASE.2025-09-07T16-13-09Z" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
USER minio
ENTRYPOINT ["minio"]
