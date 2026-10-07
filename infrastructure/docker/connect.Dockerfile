ARG VERSION=development
ARG VCS_REF=unknown

FROM quay.io/debezium/connect:3.3.1.Final AS upstream
FROM debian:bookworm-slim AS connector-download
ARG AIVEN_S3_VERSION=3.4.2
ARG AIVEN_S3_SHA256=c95fb5b82f8f5f66a9acad3e0c6fa57c1c5ac5c5cea5d07e18f4d6ff738cc8d2
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /plugins
RUN curl --fail --location --retry 3 --output aiven-s3.zip \
      "https://github.com/Aiven-Open/cloud-storage-connectors-for-apache-kafka/releases/download/v${AIVEN_S3_VERSION}/s3-sink-connector-for-apache-kafka-${AIVEN_S3_VERSION}.zip" \
    && echo "${AIVEN_S3_SHA256}  aiven-s3.zip" | sha256sum --check --strict \
    && unzip -q aiven-s3.zip \
    && mv "s3-sink-connector-for-apache-kafka-${AIVEN_S3_VERSION}" aiven-s3 \
    && rm aiven-s3.zip
FROM eclipse-temurin:21-jdk AS builder
WORKDIR /build
COPY --from=upstream /kafka/libs/ ./libs/
COPY --from=connector-download /plugins/aiven-s3/ ./aiven-s3/
COPY infrastructure/connect/*.java ./
RUN javac --release 17 -Xlint:all -cp "libs/*:aiven-s3/*" -d classes *.java \
    && mkdir -p core-classes/io/cluecdc/connect \
    && cp classes/io/cluecdc/connect/ClueSecretConfigProvider.class \
          classes/io/cluecdc/connect/ClueDeliveryTransform*.class core-classes/io/cluecdc/connect/ \
    && jar --create --file cluecdc-secrets.jar \
       -C core-classes . \
    && jar --create --file cluecdc-s3-credentials.jar \
       -C classes io/cluecdc/connect/ClueSessionCredentialsProvider.class
FROM upstream
ARG VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="ClueCDC Connect" \
      org.opencontainers.image.description="Kafka Connect with ClueCDC connector extensions" \
      org.opencontainers.image.source="https://github.com/cluedata/cluecdc" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY --from=builder /build/cluecdc-secrets.jar /kafka/libs/cluecdc-secrets.jar
COPY --from=connector-download /plugins/aiven-s3/ /kafka/connect/aiven-s3/
COPY --from=builder /build/cluecdc-s3-credentials.jar /kafka/connect/aiven-s3/cluecdc-s3-credentials.jar
