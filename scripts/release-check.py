"""Run the local release gate without publishing or creating a tag."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*command: str) -> None:
    print("\n+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def output(*command: str) -> str:
    return subprocess.check_output(command, cwd=ROOT, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate release readiness without tagging or publishing anything."
    )
    parser.add_argument(
        "--allow-dirty", action="store_true", help="Permit local changes"
    )
    parser.add_argument(
        "--skip-docker", action="store_true", help="Skip container builds"
    )
    parser.add_argument(
        "--preparation",
        action="store_true",
        help="Allow any named preparation branch (never grants release approval)",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Preparation only: omit live gates and report incomplete validation",
    )
    parser.add_argument(
        "--live-env-file",
        action="append",
        help="Disposable integration dotenv file (repeatable)",
    )
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="Preparation only: static/unit/build/audit checks",
    )
    parser.add_argument(
        "--browser", action="store_true", help="Include browser checks in the live gate"
    )
    args = parser.parse_args()

    branch = output("git", "branch", "--show-current")
    if branch not in {"main", "master"} and not (
        args.preparation and (bool(branch) or args.quality_only)
    ):
        raise SystemExit(f"Release checks must run from main or master, not {branch!r}")
    if (
        args.skip_live or args.skip_docker or args.quality_only or args.allow_dirty
    ) and not args.preparation:
        raise SystemExit(
            "Skipping checks or allowing a dirty tree requires --preparation; release gates are mandatory"
        )
    if args.quality_only and (args.browser or args.live_env_file):
        parser.error("--quality-only cannot be combined with live/browser options")
    if args.skip_live and args.browser:
        parser.error("--browser cannot be combined with --skip-live")
    if not args.allow_dirty and output("git", "status", "--porcelain"):
        raise SystemExit(
            "Working tree is not clean (use --allow-dirty while preparing changes)"
        )
    if not os.environ.get("WORKER_TEST_DATABASE_URL", "").startswith(
        "postgresql+asyncpg://"
    ):
        raise SystemExit(
            "Set WORKER_TEST_DATABASE_URL to a disposable PostgreSQL database; release tests must not skip PostgreSQL coverage"
        )
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm is required")

    run(sys.executable, "scripts/check-release-version.py")
    run(sys.executable, "scripts/check-repository.py")
    run(sys.executable, "scripts/deployment_validation.py")
    run("docker", "compose", "--env-file", ".env.example", "config", "--quiet")
    run(
        "docker",
        "compose",
        "--env-file",
        ".env.example",
        "-f",
        "compose.yaml",
        "-f",
        "compose.dev.yaml",
        "config",
        "--quiet",
    )
    run(
        "docker",
        "compose",
        "--env-file",
        ".env.example",
        "--env-file",
        ".env.test.example",
        "-f",
        "compose.yaml",
        "-f",
        "compose.test.yaml",
        "config",
        "--quiet",
    )
    run(npm, "run", "format:check")
    run(npm, "run", "lint")
    run(npm, "run", "typecheck")
    run(npm, "run", "test:web")
    run(sys.executable, "-m", "pytest", "apps/api/tests", "-q")
    run(npm, "run", "build")
    run(sys.executable, "-m", "ruff", "check", "apps/api", "scripts")
    run(sys.executable, "-m", "ruff", "format", "--check", "apps/api", "scripts")
    run(
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        "apps/api/pyproject.toml",
        "apps/api/app",
    )
    run(sys.executable, "-m", "mkdocs", "build", "--strict")
    run(npm, "audit", "--omit=dev", "--audit-level=high")
    run(npm, "audit", "--audit-level=critical")
    run(sys.executable, "-m", "pip_audit", "--local", "--skip-editable")

    if not args.skip_docker and not args.quality_only:
        if not shutil.which("docker"):
            raise SystemExit("Docker is required unless --skip-docker is supplied")
        run("docker", "build", "-t", "cluecdc-web:release-check", ".")
        run(
            "docker",
            "build",
            "-f",
            "infrastructure/docker/api.Dockerfile",
            "-t",
            "cluecdc-api:release-check",
            ".",
        )
        run(
            "docker",
            "build",
            "-f",
            "infrastructure/docker/connect.Dockerfile",
            "-t",
            "cluecdc-connect:release-check",
            ".",
        )

    if not args.skip_live and not args.quality_only:
        live = [sys.executable, "scripts/hardening_acceptance.py"]
        if args.browser:
            live.append("--browser")
        for file in args.live_env_file or []:
            live.extend(["--env-file", file])
        run(*live)
    omitted = []
    if args.quality_only or args.skip_live:
        omitted.append("live/browser acceptance")
    elif not args.browser:
        omitted.append("browser acceptance")
    if args.quality_only or args.skip_docker:
        omitted.append("release container builds")
    omitted.append("live Kubernetes acceptance and target-environment review")
    print(
        "\nLocal checks passed. Not release approval; still required: "
        + ", ".join(omitted)
        + "."
    )
    print("No tag, image, or release was published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
