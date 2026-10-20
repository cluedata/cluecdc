"""OPS-03: validate production rollout, HTTPS and CNI policy on an isolated kind cluster.

The caller owns cluster creation, Calico/Ingress installation, and cluster cleanup.
This harness refuses existing application namespaces and removes only its own fixtures.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from cdc_acceptance_support import ROOT, AcceptanceError
from deployment_runtime import docker, wait_ready
from deployment_validation import render, validate
from kafka_security_acceptance import certificates
from release_metadata import validate_metadata

TLS_PROBE = """
import http.client, json, socket, ssl, sys, time
data = json.load(sys.stdin)
context = ssl.create_default_context(cadata=data['ca'])
def request(path, body=None, origin=None, plain=False):
    if plain:
        connection = http.client.HTTPConnection(data['node_ip'], 80, timeout=10)
    else:
        connection = http.client.HTTPSConnection('cluecdc.example.com', 443, timeout=10, context=context)
        connection.sock = context.wrap_socket(socket.create_connection((data['node_ip'], 443), timeout=10),
                                              server_hostname='cluecdc.example.com')
    headers = {'Host': 'cluecdc.example.com', 'Content-Type': 'application/json'}
    if origin:
        headers['Origin'] = origin
    try:
        connection.request('POST' if body else 'GET', path,
                           body=json.dumps(body) if body else None, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()
    finally:
        connection.close()
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        status, _, _ = request('/')
        if status == 200:
            break
    except (OSError, http.client.HTTPException):
        status = 0
    time.sleep(1)
assert status == 200, 'verified HTTPS readiness failed'
assert request('/', plain=True)[0] in {301,302,307,308}, 'HTTP redirect failed'
login = {'email': 'pilot@example.com', 'password': data['password']}
status, _, body = request('/api/v1/auth/login', login, 'https://evil.example')
assert status == 403 and json.loads(body)['error']['code'] == 'CSRF_ORIGIN_REJECTED'
status, headers, body = request('/api/v1/auth/login', login, 'https://cluecdc.example.com')
assert status == 200, 'production HTTPS login failed'
cookie = next(value for key,value in headers.items() if key.lower() == 'set-cookie').lower()
assert all(flag in cookie for flag in ('secure', 'httponly', 'samesite=lax', 'path=/'))
assert json.loads(body)['user']['email'] == 'pilot@example.com'
assert data['password'] not in body
print('Verified HTTPS, redirect, CSRF and session cookie passed')
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--cluster-name", required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--web-image", required=True)
    args = parser.parse_args()
    if not args.cluster_name.startswith("cluecdc-hardening-"):
        parser.error("Use a dedicated cluecdc-hardening-* kind cluster")
    node = args.cluster_name + "-control-plane"
    info = json.loads(docker("inspect", node))[0]
    if info["Config"]["Labels"].get("io.x-k8s.kind.cluster") != args.cluster_name:
        raise AcceptanceError("Cluster node is not owned by the selected kind cluster")
    kubectl = ["kubectl", "--kubeconfig", str(args.kubeconfig.resolve())]

    def kube(
        *arguments: str,
        value: object | None = None,
        stdin: str | None = None,
        check: bool = True,
    ) -> str:
        payload = json.dumps(value) if value is not None else stdin
        result = subprocess.run(
            [*kubectl, *arguments],
            # Preserve LF in CLI password input; Windows text mode injects a literal CR.
            input=payload.encode("utf-8") if payload is not None else None,
            capture_output=True,
            check=False,
            timeout=240,
        )
        if check and result.returncode:
            raise AcceptanceError(
                f"Kubernetes command {arguments[0]} failed; output withheld"
            )
        return result.stdout.decode("utf-8").strip()

    def apply(value: dict) -> None:
        kube("apply", "-f", "-", value=value)

    if kube("get", "namespace", "cluecdc", "--ignore-not-found", "-o", "name"):
        raise AcceptanceError("Refusing an existing cluecdc namespace")
    kube(
        "-n",
        "kube-system",
        "rollout",
        "status",
        "daemonset/calico-node",
        "--timeout=180s",
    )
    kube(
        "-n",
        "ingress-nginx",
        "rollout",
        "status",
        "deployment/ingress-nginx-controller",
        "--timeout=180s",
    )
    run = uuid.uuid4().hex[:10]
    database = "cluecdc-k8s-postgres-" + run
    connect_namespace = "cluecdc-connect-" + run
    version = validate_metadata()["version"]
    api_image = f"cluecdc-k8s-{run}-api:{version}"
    web_image = f"cluecdc-k8s-{run}-web:{version}"
    for source, target in ((args.api_image, api_image), (args.web_image, web_image)):
        docker("tag", source, target)
    subprocess.run(
        [
            args.kind,
            "load",
            "docker-image",
            "--name",
            args.cluster_name,
            api_image,
            web_image,
        ],
        check=True,
        timeout=300,
    )
    environment = dict(os.environ, POSTGRES_PASSWORD=secrets.token_urlsafe(32))
    namespaces: list[str] = []
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    report = {
        "requirement": "OPS-03",
        "cluster": args.cluster_name,
        "status": "RUNNING",
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
    }
    try:
        docker(
            "run",
            "-d",
            "--name",
            database,
            "--network",
            "kind",
            "-e",
            "POSTGRES_PASSWORD",
            "-e",
            "POSTGRES_USER=cluecdc",
            "-e",
            "POSTGRES_DB=cluecdc",
            "postgres:17.9",
            environment=environment,
        )
        wait_ready(database, ["pg_isready", "-U", "cluecdc", "-d", "cluecdc"])
        database_ip = json.loads(docker("inspect", database))[0]["NetworkSettings"][
            "Networks"
        ]["kind"]["IPAddress"]
        documents = render()
        for document in documents:
            if document["kind"] == "Deployment":
                pod = document["spec"]["template"]["spec"]
                for container in [*pod.get("initContainers", []), *pod["containers"]]:
                    container["image"] = (
                        web_image if container["name"] == "web" else api_image
                    )
        if errors := validate(documents):
            raise AcceptanceError(
                "Production manifest contract failed: " + "; ".join(errors)
            )
        apply(next(item for item in documents if item["kind"] == "Namespace"))
        namespaces.append("cluecdc")
        secret_values = {
            "database-url": f"postgresql+asyncpg://cluecdc:{environment['POSTGRES_PASSWORD']}@{database_ip}:5432/cluecdc",
            "secret-encryption-key": base64.urlsafe_b64encode(
                secrets.token_bytes(32)
            ).decode(),
            "connect-secret-token": secrets.token_urlsafe(32),
            "session-secret": secrets.token_urlsafe(32),
        }
        apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "cluecdc-secrets", "namespace": "cluecdc"},
                "type": "Opaque",
                "data": {
                    key: base64.b64encode(value.encode()).decode()
                    for key, value in secret_values.items()
                },
            }
        )
        with tempfile.TemporaryDirectory(prefix="cluecdc-k8s-tls-") as temporary:
            directory = Path(temporary)
            certificates(directory, "cluecdc.example.com")
            bundle = (directory / "server.pem").read_text(encoding="utf-8")
            certificate = bundle[bundle.index("-----BEGIN CERTIFICATE-----") :]
            apply(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "cluecdc-tls", "namespace": "cluecdc"},
                    "type": "kubernetes.io/tls",
                    "data": {
                        "tls.crt": base64.b64encode(certificate.encode()).decode(),
                        "tls.key": base64.b64encode(bundle.encode()).decode(),
                    },
                }
            )
            apply({"apiVersion": "v1", "kind": "List", "items": documents})
            for deployment in ("cluecdc-api", "cluecdc-worker", "cluecdc-web"):
                kube(
                    "-n",
                    "cluecdc",
                    "rollout",
                    "status",
                    "deployment/" + deployment,
                    "--timeout=180s",
                )
            kube(
                "-n",
                "cluecdc",
                "exec",
                "deployment/cluecdc-api",
                "--",
                "alembic",
                "check",
            )
            report["production_rollout_external_postgresql_and_migration"] = "PASS"
            print(
                "Production rollout: 2 API + 2 worker + 2 web; external PostgreSQL; migration/drift passed",
                flush=True,
            )

            def probe(namespace: str, name: str, labels: dict[str, str]) -> None:
                apply(
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {
                            "name": name,
                            "namespace": namespace,
                            "labels": labels,
                        },
                        "spec": {
                            "automountServiceAccountToken": False,
                            "securityContext": {
                                "runAsNonRoot": True,
                                "runAsUser": 1000,
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "containers": [
                                {
                                    "name": "probe",
                                    "image": api_image,
                                    "imagePullPolicy": "IfNotPresent",
                                    "command": [
                                        "python",
                                        "-c",
                                        "import time; time.sleep(900)",
                                    ],
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                }
                            ],
                        },
                    }
                )
                kube(
                    "-n",
                    namespace,
                    "wait",
                    "--for=condition=Ready",
                    "pod/" + name,
                    "--timeout=90s",
                )

            api_ip = json.loads(
                kube("-n", "cluecdc", "get", "service/cluecdc-api", "-o", "json")
            )["spec"]["clusterIP"]
            web_ip = json.loads(
                kube("-n", "cluecdc", "get", "service/cluecdc-web", "-o", "json")
            )["spec"]["clusterIP"]

            def request_from(
                namespace: str, name: str, url: str, allowed: bool
            ) -> None:
                code = (
                    "import urllib.request, json\n"
                    "try:\n"
                    f" r=urllib.request.urlopen({url!r}, timeout=3); print(r.status)\n"
                    "except Exception as e: print(type(getattr(e, 'reason', e)).__name__)\n"
                )
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    result = kube(
                        "-n", namespace, "exec", name, "--", "python", "-c", code
                    )
                    if result == ("200" if allowed else "TimeoutError"):
                        return
                    time.sleep(1)
                raise AcceptanceError(
                    f"CNI boundary for {namespace}/{name} expected allowed={allowed}, got {result}"
                )

            probe("cluecdc", "unapproved-probe", {})
            request_from(
                "cluecdc",
                "unapproved-probe",
                f"http://{api_ip}:8000/health/ready",
                False,
            )
            request_from("cluecdc", "unapproved-probe", f"http://{web_ip}:3000/", False)
            apply(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": connect_namespace,
                        "labels": {"cluecdc.io/connect-client": "true"},
                    },
                }
            )
            namespaces.append(connect_namespace)
            probe(
                connect_namespace,
                "approved-connect",
                {"cluecdc.io/connect-client": "true"},
            )
            request_from(
                connect_namespace,
                "approved-connect",
                f"http://{api_ip}:8000/health/ready",
                True,
            )
            kube("label", "namespace", connect_namespace, "cluecdc.io/connect-client-")
            request_from(
                connect_namespace,
                "approved-connect",
                f"http://{api_ip}:8000/health/ready",
                False,
            )
            report["network_policy_allow_deny_and_revocation"] = "PASS"
            print(
                "Calico: unapproved API/web ingress denied, approved external Connect allowed, revoked namespace denied",
                flush=True,
            )

            password = secrets.token_urlsafe(24)
            kube(
                "-n",
                "cluecdc",
                "exec",
                "-i",
                "deployment/cluecdc-api",
                "--",
                "python",
                "-m",
                "app.cli",
                "create-admin",
                "--email",
                "pilot@example.com",
                stdin=password + "\n" + password + "\n",
            )
            node_ip = info["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--network",
                    "kind",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--user",
                    "1000:1000",
                    api_image,
                    "python",
                    "-c",
                    TLS_PROBE,
                ],
                input=json.dumps(
                    {
                        "node_ip": node_ip,
                        "ca": (directory / "ca.pem").read_text(encoding="utf-8"),
                        "password": password,
                    }
                ),
                capture_output=True,
                text=True,
                check=False,
                timeout=90,
            )
            if result.returncode:
                # The probe prints no credential or cookie values, including on assertion failure.
                raise AcceptanceError(
                    "Ingress TLS/session probe failed: " + result.stderr[-1000:]
                )
            kube("-n", "cluecdc", "rollout", "restart", "deployment/cluecdc-worker")
            kube(
                "-n",
                "cluecdc",
                "rollout",
                "status",
                "deployment/cluecdc-worker",
                "--timeout=90s",
            )
            report["https_redirect_secure_cookie_csrf_and_rolling_restart"] = "PASS"
            print(
                "NGINX HTTPS + trusted certificate + HTTP redirect + Secure cookie/CSRF + worker rolling restart passed",
                flush=True,
            )
        report["status"] = "PASS"
        return 0
    finally:
        if report["status"] != "PASS":
            diagnostic = kube(
                "get", "pods", "--all-namespaces", "-o", "wide", check=False
            )
            (artifacts / "kubernetes-diagnostics.log").write_text(
                diagnostic, encoding="utf-8"
            )
        cleanup_failed = False
        for namespace in reversed(namespaces):
            kube(
                "delete",
                "namespace",
                namespace,
                "--wait=true",
                "--timeout=90s",
                check=False,
            )
            cleanup_failed |= bool(
                kube("get", "namespace", namespace, "--ignore-not-found", "-o", "name")
            )
        database_cleanup = subprocess.run(
            ["docker", "rm", "-f", "-v", database],
            capture_output=True,
            check=False,
            timeout=60,
        )
        cleanup_failed |= database_cleanup.returncode != 0
        if report["status"] != "PASS":
            report["status"] = "FAIL"
        report["cleanup"] = "FAIL" if cleanup_failed else "PASS"
        if cleanup_failed:
            report["status"] = "FAIL"
        (artifacts / "kubernetes-acceptance.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"Kubernetes acceptance: {report['status']}; cleanup: {report['cleanup']}",
            flush=True,
        )
        if cleanup_failed:
            raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
