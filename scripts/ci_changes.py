"""Select CI gates from a trusted Git diff; uncertain comparisons run every gate."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATES = (
    "web",
    "api",
    "docs",
    "compose",
    "deployment",
    "audit_web",
    "audit_api",
    "containers",
    "arm64",
    "integration",
    "cdc",
    "s3",
    "browser",
    "browser_integration",
    "workload",
)


def classify(paths: list[str], *, full: bool = False) -> dict[str, bool]:
    selected = dict.fromkeys(GATES, full)
    for path in paths:
        if path.startswith((".github/workflows/", ".github/actions/")) or path in {
            ".github/compose.ci.yaml",
            ".github/compose.test.ci.yaml",
            "scripts/ci_changes.py",
            "scripts/ci_image_key.py",
        }:
            return dict.fromkeys(GATES, True)
        if (
            path.endswith(".md")
            or path.startswith("docs/")
            or path
            in {
                "mkdocs.yml",
                "requirements-docs.txt",
            }
        ):
            selected["docs"] = True
            continue
        if path.startswith(".github/ISSUE_TEMPLATE/") or path in {
            "LICENSE",
            "NOTICE",
            ".gitignore",
            ".gitleaks.toml",
        }:
            continue
        web = path.startswith(("apps/web/", "packages/")) or path in {
            "package.json",
            "package-lock.json",
            ".prettierrc",
            ".prettierignore",
        }
        api = path.startswith("apps/api/") or (
            path.startswith("scripts/") and path.endswith(".py")
        )
        deployment = path.startswith("deploy/kubernetes/") or path in {
            "scripts/deployment_validation.py",
            "apps/api/tests/test_deployment_validation.py",
        }
        compose = path.startswith(
            ("compose.", ".env", "infrastructure/docker/")
        ) or path in {
            "Dockerfile",
            "apps/web/Dockerfile",
            ".dockerignore",
            ".gitattributes",
        }
        runtime_web = web and not path.startswith(
            ("apps/web/tests/", "apps/web/scripts/")
        )
        runtime_api = path.startswith(
            ("apps/api/app/", "apps/api/migrations/")
        ) or path in {
            "apps/api/pyproject.toml",
            "apps/api/alembic.ini",
        }
        fixtures = path.startswith("infrastructure/")
        browser = (
            path.startswith("apps/web/tests/e2e/")
            or path == "apps/web/playwright.config.ts"
        )
        acceptance = path.startswith("scripts/") and api
        workload = path.startswith("scripts/commerce-workload/") or path in {
            "infrastructure/docker/commerce-generator.Dockerfile",
            "compose.test.yaml",
        }
        runtime = (
            runtime_web
            or runtime_api
            or compose
            or fixtures
            or browser
            or acceptance
            or workload
        )
        # Styling/assets still get real image/HTTP smoke coverage, without database fixtures.
        presentation = path.endswith(".css") or path.startswith("apps/web/public/")
        shell_ui = path in {
            "apps/web/src/components/shell.tsx",
            "apps/web/src/components/auth.tsx",
            "apps/web/src/components/brand-mark.tsx",
            "apps/web/tests/e2e/shell.spec.ts",
        } or path.startswith("apps/web/src/app/login/")
        # UI shell/styles get real browser checks against core, without CDC fixtures.
        core_browser = shell_ui or (web and path.endswith(".css"))
        full_browser = (runtime_web or browser) and not presentation and not shell_ui
        backend_acceptance = runtime_api or acceptance or compose or fixtures
        if workload:
            # Database-backed generator tests do not require CDC, S3 or a browser.
            backend_acceptance = path == "compose.test.yaml"
            full_browser = False
        storage_only = path in {
            "apps/api/app/services/object_storage.py",
            "scripts/demo-object-storage.py",
            "infrastructure/docker/minio-test.Dockerfile",
        } or path.startswith("infrastructure/minio/")
        cdc = backend_acceptance and not storage_only
        s3 = backend_acceptance
        browser_integration = full_browser or (backend_acceptance and not storage_only)
        browser_checks = core_browser or browser_integration
        integration = cdc or s3 or full_browser or workload
        audit_web = path in {"package.json", "package-lock.json"} or (
            web and path.endswith("/package.json")
        )
        audit_api = path == "apps/api/pyproject.toml"
        # PRs validate native packaging when its inputs change. Default-branch
        # pushes still smoke every runtime change on both native architectures.
        arm64 = (
            compose
            or fixtures
            or workload
            or audit_web
            or audit_api
            or (
                runtime
                and Path(path).suffix
                in {
                    ".c",
                    ".cc",
                    ".cpp",
                    ".h",
                    ".rs",
                    ".go",
                    ".java",
                    ".so",
                    ".node",
                    ".wasm",
                }
            )
        )
        values = {
            "web": web,
            "api": api,
            "compose": compose or fixtures,
            "deployment": deployment,
            "audit_web": audit_web,
            "audit_api": audit_api,
            "containers": runtime,
            "arm64": arm64,
            "integration": integration,
            "cdc": cdc,
            "s3": s3,
            "browser": browser_checks,
            "browser_integration": browser_integration,
            "workload": workload,
        }
        if not any(values.values()):
            # New/unclassified configuration must never silently lose coverage.
            return dict.fromkeys(GATES, True)
        for gate, enabled in values.items():
            selected[gate] |= enabled
    return selected


def select_event(
    event_name: str, event: dict, *, full: bool = False
) -> dict[str, bool]:
    if event_name == "schedule":
        return {**dict.fromkeys(GATES, False), "audit_web": True, "audit_api": True}
    if event_name == "workflow_dispatch":
        return (
            dict.fromkeys(GATES, True)
            if full
            else {**dict.fromkeys(GATES, False), "docs": True}
        )
    base = (
        event.get("pull_request", {}).get("base", {}).get("sha")
        if event_name == "pull_request"
        else event.get("before")
    )
    if (
        not isinstance(base, str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", base)
        or not base.strip("0")
    ):
        return classify([], full=True)
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD", "--"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return classify([], full=True)
    selected = classify(
        [path.decode("utf-8") for path in result.stdout.split(b"\0") if path]
    )
    if event_name == "push" and selected["containers"]:
        selected["arm64"] = True
    return selected


def container_matrix(gates: dict[str, bool]) -> dict[str, list[dict[str, str]]]:
    runners = [{"runner": "ubuntu-24.04", "arch": "amd64"}]
    if gates["arm64"]:
        runners.append({"runner": "ubuntu-24.04-arm", "arch": "arm64"})
    return {"include": runners}


def main() -> None:
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
    )
    gates = select_event(
        os.environ["GITHUB_EVENT_NAME"],
        event,
        full=os.environ.get("RUN_FULL_CI", "false").lower() == "true",
    )
    values = (
        "\n".join(f"{key}={str(value).lower()}" for key, value in gates.items()) + "\n"
    )
    values += (
        "container_matrix="
        + json.dumps(container_matrix(gates), separators=(",", ":"))
        + "\n"
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(values)
    print(values, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write("## Selected CI gates\n\n" + values.replace("\n", "  \n"))


if __name__ == "__main__":
    main()
