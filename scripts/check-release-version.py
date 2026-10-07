"""Validate that a release tag matches ClueCDC's package metadata."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Git tag to compare, for example v0.1.0")
    args = parser.parse_args()

    metadata = tomllib.loads(
        (ROOT / "apps/api/pyproject.toml").read_text(encoding="utf-8")
    )
    version = metadata["project"]["version"]
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"Invalid project version: {version}")
    if args.tag and args.tag != f"v{version}":
        raise SystemExit(f"Tag {args.tag!r} does not match project version v{version}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}]" not in changelog:
        raise SystemExit(f"CHANGELOG.md has no [{version}] release section")
    notes = ROOT / "docs" / "releases" / f"v{version}.md"
    if not notes.is_file():
        raise SystemExit(f"Missing release notes: {notes.relative_to(ROOT)}")

    print(f"Release metadata is consistent for v{version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
