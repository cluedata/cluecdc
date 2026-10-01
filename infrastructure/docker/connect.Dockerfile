FROM quay.io/debezium/connect:3.3.1.Final AS upstream
FROM eclipse-temurin:21-jdk AS builder
WORKDIR /build
COPY --from=upstream /kafka/libs/ ./libs/
ARG ICEBERG_CONNECT_VERSION=0.6.19
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl --fail --location --retry 3 \
      "https://github.com/databricks/iceberg-kafka-connect/releases/download/v${ICEBERG_CONNECT_VERSION}/iceberg-kafka-connect-runtime-${ICEBERG_CONNECT_VERSION}.zip" \
      --output iceberg.zip \
    && mkdir iceberg \
    && unzip -q iceberg.zip -d iceberg
COPY infrastructure/connect/*.java ./
RUN javac --release 17 -Xlint:all -cp "libs/*" -d classes *.java \
    && jar --create --file cluecdc-secrets.jar -C classes .
FROM upstream
COPY --from=builder /build/cluecdc-secrets.jar /kafka/libs/cluecdc-secrets.jar
COPY --from=builder /build/iceberg /kafka/connect/iceberg
