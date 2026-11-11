# Build the existing Connect image first; this optional fixture adds no runtime server dependency.
ARG CONNECT_IMAGE=cluecdc-kafka-connect:latest
FROM debian:bookworm-slim AS exporter
RUN apt-get update && apt-get install --no-install-recommends -y curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --retry 3 --output /jmx.jar \
    https://repo.maven.apache.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/1.0.1/jmx_prometheus_javaagent-1.0.1.jar \
    && echo '7d61f737fd661610ccc14aea79764faa1ea94a340cbc8f0029b3d2edea3d80c1  /jmx.jar' | sha256sum --check --strict
FROM ${CONNECT_IMAGE}
COPY --from=exporter /jmx.jar /opt/observability/jmx.jar
COPY infrastructure/observability/jmx-exporter.yaml /opt/observability/config.yaml
ENV JAVA_TOOL_OPTIONS="-javaagent:/opt/observability/jmx.jar=9404:/opt/observability/config.yaml"
EXPOSE 9404
