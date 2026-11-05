"""Choose independent CI jobs from changed paths; never expand to every job."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPES = (
    "api",
    "web",
    "docs",
    "compose",
    "deployment",
    "tooling",
    "workflows",
    "workload",
)
TOOLING_TESTS = {
    "test_ci_scope.py",
    "test_ci_changes.py",
    "test_ci_workflow.py",
    "test_release_tooling.py",
    "test_repository_checks.py",
    "test_hardening_gate.py",
    "test_kafka_acceptance_fixture.py",
    "test_acceptance_diagnostics.py",
    "test_acceptance_tls.py",
    "test_reliability_harness.py",
}
TOOLING_SCRIPTS = {
    "ci_scope.py",
    "ci_changes.py",
    "ci_image_key.py",
    "check-repository.py",
    "release.py",
    "release-check.py",
    "release_metadata.py",
    "check-release-version.py",
    "check_release_publication.py",
    "hardening_acceptance.py",
    "kafka_security_acceptance.py",
}


def classify(paths: list[str]) -> dict:
    result: dict = dict.fromkeys(SCOPES, False)
    images: set[str] = set()
    unknown = []
    for path in paths:
        name = Path(path).name
        if (
            path.endswith(".md")
            or path.startswith("docs/")
            or path
            in {
                "mkdocs.yml",
                "requirements-docs.txt",
            }
        ):
            result["docs"] = True
        elif path.startswith(".github/workflows/") or path.startswith(
            ".github/actions/"
        ):
            result["workflows"] = result["tooling"] = True
        elif path.startswith("scripts/") and name in TOOLING_SCRIPTS:
            result["tooling"] = True
        elif path == ".pre-commit-config.yaml":
            result["tooling"] = True
        elif path == ".editorconfig":
            result["api"] = result["web"] = True
        elif path.startswith("apps/api/tests/") and name in TOOLING_TESTS:
            result["tooling"] = True
        elif path.startswith("deploy/kubernetes/") or path in {
            "scripts/deployment_validation.py",
            "scripts/kubernetes_acceptance.py",
            "scripts/prepare_ingress_fixture.py",
            "apps/api/tests/test_deployment_validation.py",
            "apps/api/tests/test_ingress_fixture.py",
        }:
            result["deployment"] = True
        elif path.startswith("scripts/commerce-workload/"):
            result["workload"] = True
        elif path.startswith("apps/api/") or path.startswith("scripts/"):
            result["api"] = True
        elif path.startswith("packages/contracts/"):
            result["api"] = result["web"] = True
        elif path.startswith(("apps/web/", "packages/")) or path in {
            "package.json",
            "package-lock.json",
            ".prettierrc",
            ".prettierignore",
        }:
            result["web"] = True
        elif path == "Dockerfile":
            images.add("web")
        elif path.startswith("infrastructure/connect/"):
            images.add("connect")
        elif path.startswith("infrastructure/docker/"):
            image = {
                "api.Dockerfile": "api",
                "connect.Dockerfile": "connect",
                "minio-test.Dockerfile": "minio",
                "commerce-generator.Dockerfile": "workload",
            }.get(name)
            if image:
                images.add(image)
            else:
                unknown.append(path)
        elif path == ".dockerignore":
            images.update(("api", "web", "connect"))
        elif path.startswith(
            ("compose.", ".env", "infrastructure/tests/", ".github/compose.")
        ):
            result["compose"] = True
        elif path in {
            "LICENSE",
            "NOTICE",
            ".gitignore",
            ".gitattributes",
            ".gitleaks.toml",
        } or path.startswith(".github/ISSUE_TEMPLATE/"):
            pass  # Repository safety is already checked by the selector job.
        else:
            unknown.append(path)
    if unknown:
        raise ValueError(
            "Add CI scope rules for these paths: " + ", ".join(sorted(unknown))
        )
    result["images"] = sorted(images)
    return result


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT, timeout=30)


def changed_paths(event_name: str, event: dict) -> list[str]:
    if event_name == "pull_request":
        base = event.get("pull_request", {}).get("base", {}).get("sha")
    elif event_name == "push":
        base = event.get("before")
        if isinstance(base, str) and re.fullmatch(r"0{40,64}", base):
            # The first push creates all tracked paths; this is a real change set.
            return [p.decode("utf-8") for p in git("ls-files", "-z").split(b"\0") if p]
    else:
        # Manual mode compares a merge with its first parent, not an empty merge diff.
        parents = git("rev-list", "--parents", "-n", "1", "HEAD").decode().split()
        if len(parents) > 1:
            return [
                p.decode("utf-8")
                for p in git(
                    "diff",
                    "--name-only",
                    "--no-renames",
                    "-z",
                    parents[1],
                    "HEAD",
                    "--",
                ).split(b"\0")
                if p
            ]
        return [
            p.decode("utf-8")
            for p in git(
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "--no-renames",
                "-r",
                "-z",
                "HEAD",
            ).split(b"\0")
            if p
        ]
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40,64}", base):
        raise ValueError(
            "A valid comparison SHA is required; refusing to run unrelated CI jobs"
        )
    # Disable rename detection so moving a file checks both its old and new folders.
    return [
        p.decode("utf-8")
        for p in git(
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base,
            "HEAD",
            "--",
        ).split(b"\0")
        if p
    ]


def select(event_name: str, event: dict, scope: str = "changes") -> dict:
    if scope == "changes":
        return classify(changed_paths(event_name, event))
    if scope not in SCOPES:
        raise ValueError(f"Unknown CI scope: {scope}")
    return {**dict.fromkeys(SCOPES, False), scope: True, "images": []}


def main() -> None:
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
    )
    result = select(
        os.environ["GITHUB_EVENT_NAME"], event, os.environ.get("CI_SCOPE", "changes")
    )
    dockerfiles = {
        "api": "infrastructure/docker/api.Dockerfile",
        "web": "Dockerfile",
        "connect": "infrastructure/docker/connect.Dockerfile",
        "minio": "infrastructure/docker/minio-test.Dockerfile",
        "workload": "infrastructure/docker/commerce-generator.Dockerfile",
    }
    outputs = {k: str(result[k]).lower() for k in SCOPES}
    outputs["images"] = str(bool(result["images"])).lower()
    outputs["image_matrix"] = json.dumps(
        {
            "include": [
                {"component": component, "dockerfile": dockerfiles[component]}
                for component in result["images"]
            ]
        }
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write("".join(f"{key}={value}\n" for key, value in outputs.items()))
    print(json.dumps(result, indent=2))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write(
                "## Selected CI scopes\n\n```json\n"
                + json.dumps(result, indent=2)
                + "\n```\n"
            )


if __name__ == "__main__":
    main()
