"""SEC-03: isolated TLS/mTLS/SASL client matrix and authenticated source/sink CDC."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

if __package__:
    from .cdc_acceptance_support import (
        ROOT,
        AcceptanceError,
        ApiClient,
        Compose,
        wait_for,
    )
    from .deployment_runtime import docker
else:
    from cdc_acceptance_support import (
        ROOT,
        AcceptanceError,
        ApiClient,
        Compose,
        wait_for,
    )
    from deployment_runtime import docker


def poll_docker(*arguments: str) -> str:
    try:
        return docker(*arguments)
    except RuntimeError as error:
        raise AcceptanceError(str(error)) from None


FIXTURE_GROUP = "65532"
FIXTURE_FILES = ("server.pem", "ca.pem", "ca.properties", "server.properties")
STAGE_FILES = """
import json, os, pathlib, sys
os.umask(0o077)
root = pathlib.Path('/fixture')
root.chmod(0o750)
os.chown(root, 0, 65532)
for name, contents in json.load(sys.stdin).items():
    if name not in {'server.pem', 'ca.pem', 'ca.properties', 'server.properties'}:
        raise ValueError('Unexpected fixture file')
    target = root / name
    target.write_text(contents, encoding='utf-8')
    os.chown(target, 0, 65532)
    target.chmod(0o640)
"""


def stage_fixture(volume: str, image: str, directory: Path) -> None:
    """Copy private files over stdin into a Linux-owned volume, never world-readable."""
    docker(
        "run",
        "-i",
        "--name",
        volume + "-init",
        "--user",
        "0:0",
        "--mount",
        f"type=volume,source={volume},target=/fixture",
        "--entrypoint",
        "python",
        image,
        "-c",
        STAGE_FILES,
        stdin=json.dumps(
            {
                name: (directory / name).read_text(encoding="utf-8")
                for name in FIXTURE_FILES
            }
        ),
    )
    docker("rm", volume + "-init")


def readiness_probe(container: str, *command: str) -> str:
    state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
    if not state["Running"]:
        # RuntimeError escapes wait_for's transient AcceptanceError retry loop.
        raise RuntimeError(
            f"Fixture stopped before readiness: exit={state['ExitCode']}, "
            f"oom={bool(state.get('OOMKilled'))}"
        )
    return poll_docker("exec", container, *command)


def redact_diagnostics(value: str, sensitive: tuple[str, ...]) -> str:
    for secret in sensitive:
        if not secret:
            continue
        value = value.replace(secret, "[REDACTED]")
        # Java logs can print PEM values with escaped newlines or line prefixes.
        value = value.replace(secret.replace("\n", r"\n"), "[REDACTED]")
        if "-----BEGIN" in secret:
            for line in secret.splitlines():
                if line:
                    value = value.replace(line, "[REDACTED]")
    return value


def collect_diagnostics(containers: list[str], sensitive: tuple[str, ...]) -> str:
    diagnostic = []
    for container in containers:
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", "80", container],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            diagnostic.append(result.stdout + result.stderr)
        except (OSError, subprocess.SubprocessError):
            diagnostic.append("Fixture logs unavailable")
    return redact_diagnostics("\n".join(diagnostic), sensitive)


def cleanup_fixture(containers: list[str], volume: str | None) -> bool:
    ok = True
    for command in [
        *(["docker", "rm", "-f", "-v", name] for name in reversed(containers)),
        *([["docker", "volume", "rm", volume]] if volume else []),
    ]:
        try:
            result = subprocess.run(
                command, capture_output=True, check=False, timeout=60
            )
            ok = ok and result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    return ok


PROBE = """
import asyncio, json, sys
from app.adapters.kafka import KafkaExplorer
from app.core.errors import DomainError
from app.models.entities import KafkaCluster
data = json.load(sys.stdin)
async def main():
    results = []
    for case in data:
        cluster = KafkaCluster(bootstrap_servers=case['servers'],
            security_protocol=case['protocol'],
            security_config_json={'sasl_mechanism': case.get('mechanism', '')})
        explorer = KafkaExplorer(case['credentials'])
        for client, operation in [('aiokafka', explorer.topics),
                                   ('librdkafka', explorer.consumer_groups)]:
            try:
                await operation(cluster)
            except DomainError as error:
                if case['expected']:
                    raise RuntimeError(case['name'] + '/' + client + ': ' + error.code) from None
            else:
                if not case['expected']:
                    raise RuntimeError(case['name'] + '/' + client + ': accepted invalid security')
            results.append(case['name'] + '/' + client)
    print(json.dumps(results))
asyncio.run(main())
"""


def certificates(directory: Path, hostname: str) -> dict[str, str]:
    """Generate short-lived fixture CA, broker and client certificates locally."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "ClueCDC acceptance CA")]
    )
    now = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    result = {"ssl_ca_pem": ca.public_bytes(serialization.Encoding.PEM).decode()}
    for identity in ("broker", "client"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
            )
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=2))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.SERVER_AUTH
                        if identity == "broker"
                        else ExtendedKeyUsageOID.CLIENT_AUTH
                    ]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        if identity == "broker":
            (directory / "server.pem").write_text(key_pem + cert_pem, encoding="utf-8")
        else:
            result.update(ssl_certificate_pem=cert_pem, ssl_key_pem=key_pem)
    (directory / "ca.pem").write_text(result["ssl_ca_pem"], encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--env-file", action="append", type=Path, required=True)
    parser.add_argument("--api-url", default="http://localhost:3000/api/v1")
    args = parser.parse_args()
    compose = Compose(args.compose_project, args.env_file)
    api_id = compose.run("ps", "-q", "cluecdc-api")
    connect_id = compose.run("ps", "-q", "kafka-connect")
    if not api_id or not connect_id:
        raise AcceptanceError(
            "Start the isolated integration stack before Kafka security acceptance"
        )
    network = json.loads(docker("inspect", api_id))[0]["NetworkSettings"]["Networks"]
    network_name = next(iter(network))
    connect_info = json.loads(docker("inspect", connect_id))[0]
    connect_env = dict(
        item.split("=", 1) for item in connect_info["Config"]["Env"] if "=" in item
    )
    password = secrets.token_urlsafe(32)
    run = uuid.uuid4().hex[:12]
    broker = f"cluecdc-secure-{run}"
    connect = f"cluecdc-secure-connect-{run}"
    containers: list[str] = []
    volume = f"cluecdc-secure-files-{run}"
    volume_created = False
    report = {"requirement": "SEC-03", "status": "RUNNING", "phase": "fixture-files"}
    started = time.monotonic()
    temporary_root = ROOT / ".codex-tmp"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="kafka-security-", dir=temporary_root
    ) as temporary:
        directory = Path(temporary)
        certs = certificates(directory, broker)
        (directory / "ca.properties").write_text(
            "ca=" + certs["ssl_ca_pem"].replace("\n", r"\n") + "\n", encoding="utf-8"
        )
        config = f'''process.roles=broker,controller
node.id=1
controller.quorum.voters=1@{broker}:9093
controller.listener.names=CONTROLLER
listeners=INTERNAL://:9092,CONTROLLER://:9093,SECURE://:9094,TLS://:9095,MTLS://:9096,CLEAR://:9097
advertised.listeners=INTERNAL://{broker}:9092,SECURE://{broker}:9094,TLS://{broker}:9095,MTLS://{broker}:9096,CLEAR://{broker}:9097
listener.security.protocol.map=INTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT,SECURE:SASL_SSL,TLS:SSL,MTLS:SSL,CLEAR:SASL_PLAINTEXT
inter.broker.listener.name=INTERNAL
sasl.enabled.mechanisms=PLAIN,SCRAM-SHA-256,SCRAM-SHA-512
listener.name.secure.plain.sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required user_pilot="{password}";
listener.name.secure.scram-sha-256.sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required;
listener.name.secure.scram-sha-512.sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required;
listener.name.clear.plain.sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required user_pilot="{password}";
listener.name.clear.scram-sha-256.sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required;
listener.name.clear.scram-sha-512.sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required;
ssl.keystore.type=PEM
ssl.keystore.location=/fixture/server.pem
ssl.truststore.type=PEM
ssl.truststore.location=/fixture/ca.pem
listener.name.mtls.ssl.client.auth=required
log.dirs=/tmp/kafka-data
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
group.initial.rebalance.delay.ms=0
num.partitions=1
'''
        (directory / "server.properties").write_text(config, encoding="utf-8")
        fixture_env = dict(
            os.environ,
            KAFKA_TEST_PASSWORD=password,
            KAFKA_HEAP_OPTS="-Xmx384M -Xms256M",
        )
        try:
            for name in (broker, connect, volume + "-init"):
                if docker("ps", "-aq", "--filter", f"name=^/{name}$"):
                    raise AcceptanceError(
                        "Refusing an existing secure fixture container"
                    )
            # Host TemporaryDirectory is 0700 on Linux and cannot be read by Kafka UID 1000.
            # A Docker volume has portable ownership, with access limited to an explicit group.
            if docker("volume", "ls", "-q", "--filter", f"name=^{volume}$"):
                raise AcceptanceError("Refusing an existing secure fixture volume")
            docker("volume", "create", "--label", f"cluecdc.fixture={run}", volume)
            volume_created = True
            api_image = json.loads(docker("inspect", api_id))[0]["Image"]
            containers.append(volume + "-init")
            stage_fixture(volume, api_image, directory)
            containers.remove(volume + "-init")
            report["phase"] = "broker-startup"
            containers.append(broker)
            docker(
                "run",
                "-d",
                "--name",
                broker,
                "--network",
                network_name,
                "--mount",
                f"type=volume,source={volume},target=/fixture,readonly",
                "--group-add",
                FIXTURE_GROUP,
                "-e",
                "KAFKA_TEST_PASSWORD",
                "-e",
                "KAFKA_HEAP_OPTS",
                "--entrypoint",
                "sh",
                "apache/kafka:4.1.2",
                "-ec",
                "/opt/kafka/bin/kafka-storage.sh format -t MkU3OEVBNTcwNTJENDM2Qk "
                "-c /fixture/server.properties "
                '--add-scram "SCRAM-SHA-256=[name=pilot,password=$KAFKA_TEST_PASSWORD]" '
                '--add-scram "SCRAM-SHA-512=[name=pilot,password=$KAFKA_TEST_PASSWORD]" && '
                "exec /opt/kafka/bin/kafka-server-start.sh /fixture/server.properties",
                environment=fixture_env,
            )
            wait_for(
                lambda: readiness_probe(
                    broker,
                    "/opt/kafka/bin/kafka-topics.sh",
                    "--bootstrap-server",
                    f"{broker}:9092",
                    "--list",
                ),
                lambda output: True,
                "secure Kafka broker",
                120,
            )
            report["phase"] = "client-matrix"
            cases = []
            credentials = {
                "username": "pilot",
                "password": password,
                "ssl_ca_pem": certs["ssl_ca_pem"],
            }
            for mechanism in ("PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"):
                cases.append(
                    dict(
                        name=mechanism,
                        servers=f"{broker}:9094",
                        protocol="SASL_SSL",
                        mechanism=mechanism,
                        credentials=credentials,
                        expected=True,
                    )
                )
                cases.append(
                    dict(
                        name="development-SASL_PLAINTEXT/" + mechanism,
                        servers=f"{broker}:9097",
                        protocol="SASL_PLAINTEXT",
                        mechanism=mechanism,
                        credentials={"username": "pilot", "password": password},
                        expected=True,
                    )
                )
            cases.extend(
                [
                    dict(
                        name="TLS",
                        servers=f"{broker}:9095",
                        protocol="SSL",
                        credentials={"ssl_ca_pem": certs["ssl_ca_pem"]},
                        expected=True,
                    ),
                    dict(
                        name="mTLS",
                        servers=f"{broker}:9096",
                        protocol="SSL",
                        credentials=certs,
                        expected=True,
                    ),
                    dict(
                        name="invalid-password",
                        servers=f"{broker}:9094",
                        protocol="SASL_SSL",
                        mechanism="PLAIN",
                        credentials={**credentials, "password": "wrong"},
                        expected=False,
                    ),
                    dict(
                        name="untrusted-CA",
                        servers=f"{broker}:9095",
                        protocol="SSL",
                        credentials={},
                        expected=False,
                    ),
                    dict(
                        name="missing-client-certificate",
                        servers=f"{broker}:9096",
                        protocol="SSL",
                        credentials={"ssl_ca_pem": certs["ssl_ca_pem"]},
                        expected=False,
                    ),
                ]
            )
            result = subprocess.run(
                ["docker", "exec", "-i", api_id, "python", "-c", PROBE],
                input=json.dumps(cases),
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )
            if result.returncode:
                raise AcceptanceError(
                    "Kafka client security probe failed; "
                    + redact_diagnostics(
                        result.stderr,
                        (password, certs["ssl_key_pem"], certs["ssl_ca_pem"]),
                    )[-500:]
                )
            matrix = json.loads(result.stdout.splitlines()[-1])
            report["client_checks"] = matrix
            print(
                f"TLS/mTLS/SASL and rejection matrix: {len(matrix)} client checks passed",
                flush=True,
            )

            report["phase"] = "connect-startup"
            worker = dict(connect_env)
            worker.update(
                BOOTSTRAP_SERVERS=f"{broker}:9094",
                GROUP_ID=f"secure-{run}",
                ADVERTISED_HOST_NAME=connect,
                REST_ADVERTISED_HOST_NAME=connect,
                CLUECDC_SECRETS_TOKEN=connect_env["CLUECDC_SECRETS_TOKEN"],
                CONNECT_CONFIG_PROVIDERS="cluecdc,file",
                CONNECT_CONFIG_PROVIDERS_FILE_CLASS="org.apache.kafka.common.config.provider.FileConfigProvider",
            )
            for prefix in ("CONNECT_", "CONNECT_PRODUCER_", "CONNECT_CONSUMER_"):
                worker.update(
                    {
                        prefix + "SECURITY_PROTOCOL": "SASL_SSL",
                        prefix + "SASL_MECHANISM": "SCRAM-SHA-512",
                        prefix
                        + "SASL_JAAS_CONFIG": 'org.apache.kafka.common.security.scram.ScramLoginModule required username="pilot" password="'
                        + password
                        + '";',
                        prefix + "SSL_TRUSTSTORE_TYPE": "PEM",
                        prefix
                        + "SSL_TRUSTSTORE_CERTIFICATES": "${file:/fixture/ca.properties:ca}",
                    }
                )
            env_flags = [part for name in worker for part in ("-e", name)]
            containers.append(connect)
            docker(
                "run",
                "-d",
                "--name",
                connect,
                "--network",
                network_name,
                "--mount",
                f"type=volume,source={volume},target=/fixture,readonly",
                "--group-add",
                FIXTURE_GROUP,
                *env_flags,
                connect_info["Config"]["Image"],
                environment={**os.environ, **worker},
            )
            wait_for(
                lambda: readiness_probe(
                    connect,
                    "curl",
                    "-fsS",
                    "http://localhost:8083/connector-plugins",
                ),
                lambda output: bool(json.loads(output)),
                "authenticated Kafka Connect",
                180,
            )
            report["phase"] = "source-sink-matrix"
            environment = dict(os.environ)
            environment["CLUECDC_SECURE_KAFKA_JSON"] = json.dumps(
                {
                    "bootstrap_servers": f"{broker}:9094",
                    "security_protocol": "SASL_SSL",
                    "sasl_mechanism": "SCRAM-SHA-512",
                    **credentials,
                }
            )
            command = [
                sys.executable,
                str(ROOT / "scripts/cdc-integrity.py"),
                "--compose-project",
                args.compose_project,
                "--api-url",
                args.api_url,
                "--kafka-config-env",
                "CLUECDC_SECURE_KAFKA_JSON",
                "--connect-internal-url",
                f"http://{connect}:8083",
                "--artifact",
                str(ROOT / "artifacts/cdc-secure-integrity.json"),
            ]
            for file in args.env_file:
                command.extend(["--env-file", str(file)])
            subprocess.run(command, cwd=ROOT, env=environment, check=True, timeout=900)
            report["source_sink_matrix"] = "4/4 over SASL_SSL SCRAM-SHA-512"
            report["phase"] = "secret-boundaries"
            # Public API serialization and runtime logs must not contain plaintext fixture credentials.
            api = ApiClient(
                args.api_url, [password, certs["ssl_key_pem"], certs["ssl_ca_pem"]]
            )
            api.request("/kafka/clusters")
            for container in (api_id, connect):
                logs = docker("logs", container)
                if password in logs or certs["ssl_key_pem"] in logs:
                    raise AcceptanceError(
                        "Kafka fixture credentials appeared in runtime logs"
                    )
            report["status"] = "PASS"
            print(
                "Authenticated source/sink CDC and secret redaction: passed", flush=True
            )
        finally:
            failed = sys.exc_info()[0] is not None
            if failed:
                report["status"] = "FAIL"
                logs = collect_diagnostics(
                    containers, (password, certs["ssl_key_pem"], certs["ssl_ca_pem"])
                )
                (ROOT / "artifacts").mkdir(exist_ok=True)
                try:
                    (ROOT / "artifacts/kafka-security-diagnostics.log").write_text(
                        logs, encoding="utf-8"
                    )
                except OSError:
                    report["diagnostics"] = "UNAVAILABLE"
            cleanup_ok = cleanup_fixture(containers, volume if volume_created else None)
            report["cleanup"] = "PASS" if cleanup_ok else "FAIL"
            report["seconds"] = round(time.monotonic() - started, 1)
            if not cleanup_ok:
                report["status"] = "FAIL"
            (ROOT / "artifacts").mkdir(exist_ok=True)
            (ROOT / "artifacts/kafka-security.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            if not cleanup_ok and not failed:
                raise AcceptanceError("Secure fixture cleanup failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
