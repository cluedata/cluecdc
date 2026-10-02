FROM quay.io/debezium/connect:3.3.1.Final AS upstream
FROM eclipse-temurin:21-jdk AS builder
WORKDIR /build
COPY --from=upstream /kafka/libs/ ./libs/
COPY infrastructure/connect/*.java ./
RUN javac --release 17 -Xlint:all -cp "libs/*" -d classes *.java \
    && jar --create --file cluecdc-secrets.jar -C classes .
FROM upstream
COPY --from=builder /build/cluecdc-secrets.jar /kafka/libs/cluecdc-secrets.jar
