"""Validate that a release tag matches ClueCDC's package metadata."""

from __future__ import annotations

import argparse

from release_metadata import validate_metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Git tag to compare with the package version")
    parser.add_argument(
        "--require-final",
        action="store_true",
        help="Reject draft notes and an undated changelog",
    )
    args = parser.parse_args()

    try:
        metadata = validate_metadata(tag=args.tag, require_final=args.require_final)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Release metadata is consistent for {metadata['tag']}.")
    for warning in metadata["warnings"]:
        print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
