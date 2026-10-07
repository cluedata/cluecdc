"""Run the local release gate without publishing or creating a tag."""

from __future__ import annotations

import argparse
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
    args = parser.parse_args()

    branch = output("git", "branch", "--show-current")
    if branch not in {"main", "master"}:
        raise SystemExit(f"Release checks must run from main or master, not {branch!r}")
    if not args.allow_dirty and output("git", "status", "--porcelain"):
        raise SystemExit(
            "Working tree is not clean (use --allow-dirty while preparing changes)"
        )
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm is required")

    run(sys.executable, "scripts/check-release-version.py")
    run(sys.executable, "scripts/check-repository.py")
    run("docker", "compose", "config", "--quiet")
    run(
        "docker",
        "compose",
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

    if not args.skip_docker:
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

    print("\nRelease checks passed. No tag, image, or release was published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
