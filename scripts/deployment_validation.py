"""Render and enforce the production Kubernetes deployment contract."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
KUSTOMIZE = ROOT / "deploy" / "kubernetes"
EXPECTED_DEPLOYMENTS = {"cluecdc-api", "cluecdc-worker", "cluecdc-web"}
REQUIRED_SECRET_KEYS = {
    "database-url",
    "secret-encryption-key",
    "connect-secret-token",
    "session-secret",
}
PINNED_IMAGE = re.compile(
    r"(?:@sha256:[0-9a-f]{64}|:\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$"
)


def render(directory: Path = KUSTOMIZE) -> list[dict[str, Any]]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError(
            "kubectl is required to render the production Kustomize base"
        )
    result = subprocess.run(
        [kubectl, "kustomize", str(directory)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"Kustomize rendering failed: {result.stderr.strip()}")
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def _name(document: dict[str, Any]) -> str:
    return str(document.get("metadata", {}).get("name", ""))


def _secure_container(
    container: dict[str, Any], workload: str, errors: list[str]
) -> None:
    name = container.get("name", "unnamed")
    prefix = f"{workload}/{name}"
    image = str(container.get("image", ""))
    if not PINNED_IMAGE.search(image):
        errors.append(f"{prefix}: image must use an exact semantic version or digest")
    security = container.get("securityContext", {})
    if security.get("allowPrivilegeEscalation") is not False:
        errors.append(f"{prefix}: privilege escalation must be disabled")
    if security.get("readOnlyRootFilesystem") is not True:
        errors.append(f"{prefix}: root filesystem must be read-only")
    if "ALL" not in security.get("capabilities", {}).get("drop", []):
        errors.append(f"{prefix}: all Linux capabilities must be dropped")
    resources = container.get("resources", {})
    if not resources.get("requests") or not resources.get("limits"):
        errors.append(f"{prefix}: resource requests and limits are required")


def validate(documents: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for document in documents:
        by_kind.setdefault(str(document.get("kind")), []).append(document)

    if by_kind.get("Secret"):
        errors.append("rendered production manifests must not contain Secret values")
    if by_kind.get("StatefulSet"):
        errors.append("production base must use external metadata PostgreSQL")

    deployments = {_name(item): item for item in by_kind.get("Deployment", [])}
    if set(deployments) != EXPECTED_DEPLOYMENTS:
        errors.append(
            "production base must contain only API, worker, and web Deployments"
        )

    secret_keys: set[str] = set()
    for workload, deployment in deployments.items():
        spec = deployment.get("spec", {})
        if spec.get("replicas", 0) < 2:
            errors.append(f"{workload}: at least two replicas are required")
        pod = spec.get("template", {}).get("spec", {})
        if pod.get("automountServiceAccountToken") is not False:
            errors.append(
                f"{workload}: service account token automount must be disabled"
            )
        if pod.get("terminationGracePeriodSeconds", 0) < 30:
            errors.append(
                f"{workload}: termination grace period must be at least 30 seconds"
            )
        pod_security = pod.get("securityContext", {})
        if pod_security.get("runAsNonRoot") is not True:
            errors.append(f"{workload}: runAsNonRoot is required")
        if pod_security.get("seccompProfile", {}).get("type") != "RuntimeDefault":
            errors.append(f"{workload}: RuntimeDefault seccomp is required")

        containers = pod.get("containers", [])
        if len(containers) != 1:
            errors.append(f"{workload}: exactly one application container is expected")
        for container in [*pod.get("initContainers", []), *containers]:
            _secure_container(container, workload, errors)
            for variable in container.get("env", []):
                reference = variable.get("valueFrom", {}).get("secretKeyRef")
                if reference:
                    if reference.get("name") != "cluecdc-secrets":
                        errors.append(f"{workload}: unexpected Secret reference")
                    secret_keys.add(str(reference.get("key")))
        for container in containers:
            for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
                if probe not in container:
                    errors.append(f"{workload}: {probe} is required")

    api_init = (
        deployments.get("cluecdc-api", {})
        .get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("initContainers", [])
    )
    if not any(
        container.get("command") == ["alembic", "upgrade", "head"]
        for container in api_init
    ):
        errors.append("API must run the guarded Alembic migration init container")
    if not REQUIRED_SECRET_KEYS.issubset(secret_keys):
        errors.append("all required production credentials must use cluecdc-secrets")

    config_maps = {_name(item): item for item in by_kind.get("ConfigMap", [])}
    config = config_maps.get("cluecdc-config", {}).get("data", {})
    if (
        config.get("ENVIRONMENT") != "production"
        or config.get("AUTH_MODE") != "session"
    ):
        errors.append("production session authentication must be configured")
    if config.get("BOOTSTRAP_DEFAULT_ADMIN", "false").lower() != "false":
        errors.append("development Admin bootstrap must be disabled")
    if not str(config.get("PUBLIC_URL", "")).startswith("https://"):
        errors.append("PUBLIC_URL must use HTTPS")

    for service in by_kind.get("Service", []):
        if service.get("spec", {}).get("type", "ClusterIP") != "ClusterIP":
            errors.append(
                f"{_name(service)}: only private ClusterIP Services are allowed"
            )
    if any(
        _name(item) in {"kafka-connect", "cluecdc-metadata"}
        for item in by_kind.get("Service", [])
    ):
        errors.append(
            "production base must not bundle Kafka Connect or metadata PostgreSQL"
        )

    ingresses = by_kind.get("Ingress", [])
    if len(ingresses) != 1 or not ingresses[0].get("spec", {}).get("tls"):
        errors.append("exactly one TLS Ingress is required")
    policy_names = {_name(item) for item in by_kind.get("NetworkPolicy", [])}
    if not {"default-deny-ingress", "allow-web-ingress", "allow-api-clients"}.issubset(
        policy_names
    ):
        errors.append("required ingress NetworkPolicies are missing")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kustomize-dir", type=Path, default=KUSTOMIZE)
    args = parser.parse_args()
    try:
        documents = render(args.kustomize_dir)
    except RuntimeError as error:
        print(f"Deployment validation infrastructure unavailable: {error}")
        return 2
    errors = validate(documents)
    if errors:
        print("Production deployment validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"Production deployment validation passed for {len(documents)} rendered resources."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
