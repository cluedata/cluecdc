"""Shared, read-only release metadata and candidate inspection."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path

import tomllib

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


def acceptance_status(root: Path, configuration: dict) -> dict:
    """Read explicitly configured gates; never manufacture or rewrite evidence."""
    if not isinstance(configuration, dict):
        raise ValueError("Release acceptance configuration must be a table")
    if not configuration:
        return {"path": None, "status": "NOT_REQUIRED", "gates": [], "blockers": []}
    matrix = configuration.get("acceptance_matrix")
    required = configuration.get("required_acceptance_gates")
    if (
        not isinstance(matrix, str)
        or not matrix
        or not isinstance(required, list)
        or not required
        or any(not isinstance(gate, str) or not gate for gate in required)
        or len(set(required)) != len(required)
    ):
        raise ValueError(
            "Release acceptance requires a matrix path and unique required gates"
        )
    path = (root / matrix).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Release acceptance matrix must be inside the repository")
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(
            "Release acceptance matrix must contain a list of gate records"
        )
    gates = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("gate_id"), str):
            raise ValueError("Invalid release acceptance gate record")
        identifier = record["gate_id"]
        if identifier in gates:
            raise ValueError(f"Duplicate release acceptance gate: {identifier}")
        if record.get("status") not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
            raise ValueError(f"Invalid release acceptance status: {identifier}")
        if record["status"] == "PASS":
            evidence = record.get("evidence_path")
            references = [evidence] if isinstance(evidence, str) else evidence
            if (
                not isinstance(references, list)
                or not references
                or any(
                    not isinstance(item, str) or not item.strip() for item in references
                )
            ):
                raise ValueError(
                    f"Passing release gate requires evidence references: {identifier}"
                )
        gates[identifier] = record
    missing = sorted(set(required) - gates.keys())
    unexpected = sorted(gates.keys() - set(required))
    if missing or unexpected:
        raise ValueError(
            f"Release acceptance gates do not match configuration; "
            f"missing={missing}, unexpected={unexpected}"
        )
    blockers = [
        f"{identifier}: {gates[identifier]['status']} - "
        f"{gates[identifier].get('failure_reason') or 'No passing execution evidence'}"
        for identifier in required
        if gates[identifier]["status"] != "PASS"
    ]
    return {
        "path": matrix,
        "status": "BLOCKED" if blockers else "PASS",
        "gates": [
            {"gate_id": identifier, "status": gates[identifier]["status"]}
            for identifier in required
        ],
        "blockers": blockers,
    }


def validate_metadata(
    root: Path = ROOT,
    *,
    tag: str | None = None,
    require_final: bool = False,
    require_acceptance: bool = False,
) -> dict:
    project = tomllib.loads(
        (root / "apps/api/pyproject.toml").read_text(encoding="utf-8")
    )
    version = project["project"]["version"]
    if not SEMVER.fullmatch(version):
        raise ValueError(f"Invalid project version: {version}")
    expected_tag = f"v{version}"
    if tag and tag != expected_tag:
        raise ValueError(f"Tag {tag!r} does not match project version {expected_tag}")
    lock = tomllib.loads((root / "apps/api/uv.lock").read_text(encoding="utf-8"))
    packages = [
        item for item in lock.get("package", []) if item.get("name") == "cluecdc-api"
    ]
    if len(packages) != 1 or packages[0].get("version") != version:
        raise ValueError(f"apps/api/uv.lock must pin cluecdc-api to {version}")
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
    title = notes.partition("\n")[0]
    if not re.fullmatch(rf"# ClueCDC {re.escape(expected_tag)}(?: — [^\n]+)?", title):
        raise ValueError(
            f"Release notes title must start with '# ClueCDC {expected_tag}'"
        )
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
    acceptance = acceptance_status(
        root, project.get("tool", {}).get("cluecdc", {}).get("release", {})
    )
    if require_acceptance and acceptance["blockers"]:
        raise ValueError(
            "Mandatory release acceptance is incomplete:\n- "
            + "\n- ".join(acceptance["blockers"])
        )
    return {
        "version": version,
        "tag": expected_tag,
        "notes": notes_path.as_posix(),
        "images": [f"ghcr.io/cluedata/cluecdc-{name}:{version}" for name in COMPONENTS],
        "warnings": warnings,
        "metadata_final": not warnings,
        "acceptance": acceptance,
    }


def git_output(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=root, text=True, timeout=30
    ).strip()


def inspect_candidate(
    root: Path = ROOT, *, tag: str | None = None, require_final: bool = False
) -> dict:
    result = validate_metadata(
        root, tag=tag, require_final=require_final, require_acceptance=require_final
    )
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
    if require_final and dirty:
        raise ValueError(
            "Release candidate has uncommitted changes; commit and revalidate the exact SHA"
        )
    if target and target != sha:
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
