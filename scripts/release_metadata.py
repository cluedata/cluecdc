"""Shared, read-only release metadata and candidate inspection."""

from __future__ import annotations

import re
import subprocess
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NUMBER = r"(?:0|[1-9][0-9]*)"
IDENTIFIER = rf"(?:{NUMBER}|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = re.compile(
    rf"{NUMBER}\.{NUMBER}\.{NUMBER}(?:-{IDENTIFIER}(?:\.{IDENTIFIER})*)?"
)
COMPONENTS = ("web", "api", "connect")
IMAGE_FILES = {
    "api.yaml": "api",
    "worker.yaml": "api",
    "web.yaml": "web",
    "connect.yaml": "connect",
}


def validate_metadata(
    root: Path = ROOT, *, tag: str | None = None, require_final: bool = False
) -> dict:
    version = tomllib.loads(
        (root / "apps/api/pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    if not SEMVER.fullmatch(version):
        raise ValueError(f"Invalid project version: {version}")
    expected_tag = f"v{version}"
    if tag and tag != expected_tag:
        raise ValueError(f"Tag {tag!r} does not match project version {expected_tag}")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = re.findall(
        rf"^## \[{re.escape(version)}\] - (.+)$", changelog, re.MULTILINE
    )
    if len(sections) != 1:
        raise ValueError(
            f"CHANGELOG.md must contain exactly one [{version}] release section"
        )
    notes_path = Path("docs/releases") / f"{expected_tag}.md"
    notes = (root / notes_path).read_text(encoding="utf-8")
    if not notes.startswith(f"# ClueCDC {expected_tag}\n"):
        raise ValueError(f"Release notes title must be '# ClueCDC {expected_tag}'")
    warnings = []
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sections[0]):
            raise ValueError("Expected ISO release date")
        date.fromisoformat(sections[0])
    except ValueError:
        warnings.append(
            "Changelog release date is not finalized (YYYY-MM-DD required)."
        )
    if re.search(r"^Unreleased draft\b", notes, re.MULTILINE | re.IGNORECASE):
        warnings.append("Release notes are still an Unreleased draft.")
    for filename, component in IMAGE_FILES.items():
        path = Path("deploy/kubernetes") / filename
        images = re.findall(
            r"^\s*image:\s*([^\s]+)",
            (root / path).read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        expected_image = f"ghcr.io/cluedata/cluecdc-{component}:{version}"
        if not images or any(image.strip("\"'") != expected_image for image in images):
            raise ValueError(
                f"{path.as_posix()} must pin all ClueCDC images to {version}"
            )
    if require_final and warnings:
        raise ValueError("Release metadata is not final: " + " ".join(warnings))
    return {
        "version": version,
        "tag": expected_tag,
        "notes": notes_path.as_posix(),
        "images": [f"ghcr.io/cluedata/cluecdc-{name}:{version}" for name in COMPONENTS],
        "warnings": warnings,
    }


def git_output(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=root, text=True, timeout=30
    ).strip()


def inspect_candidate(root: Path = ROOT) -> dict:
    result = validate_metadata(root)
    sha = git_output(root, "rev-parse", "HEAD")
    tag = result["tag"]
    # Distinguish a missing local tag (1) from an actual Git failure.
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/tags/{tag}"],
        cwd=root,
        check=False,
        timeout=30,
    )
    if exists.returncode not in (0, 1):
        raise RuntimeError(f"Could not inspect local tag {tag}")
    target = (
        git_output(root, "rev-parse", f"refs/tags/{tag}^{{commit}}")
        if not exists.returncode
        else None
    )
    dirty = bool(git_output(root, "status", "--porcelain"))
    if target:
        result["warnings"].append(
            f"Local tag {tag} already exists at {target}; never move or reuse a release tag."
        )
    if dirty:
        result["warnings"].append(
            "Working tree is dirty; this is not an immutable candidate."
        )
    result.update(
        git_sha=sha,
        branch=git_output(root, "branch", "--show-current"),
        dirty=dirty,
        local_tag_target=target,
        new_local_tag_available=target is None,
        remote_state="NOT_CHECKED: fetch/review remote tags and registry before approval",
        release_approval="NOT_GRANTED: CI and target-environment review are still required",
    )
    return result
