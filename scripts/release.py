"""Inspect a release candidate locally. Never fetch, tag, push or publish."""

from __future__ import annotations

import argparse
import json

from release_metadata import inspect_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan"])
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable evidence"
    )
    parser.add_argument(
        "--require-new-tag",
        action="store_true",
        help="Fail if the version's local tag exists",
    )
    args = parser.parse_args()
    try:
        candidate = inspect_candidate()
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    if args.json:
        print(json.dumps(candidate, indent=2))
    else:
        print(f"Candidate: {candidate['tag']} at {candidate['git_sha']}")
        print(
            f"Branch: {candidate['branch'] or '(detached)'}; dirty: {candidate['dirty']}"
        )
        print(f"Notes: {candidate['notes']}")
        for image in candidate["images"]:
            print(f"Planned image (not published): {image}")
        for warning in candidate["warnings"]:
            print(f"WARNING: {warning}")
        print(candidate["remote_state"])
        print(candidate["release_approval"])
    return int(args.require_new_tag and not candidate["new_local_tag_available"])


if __name__ == "__main__":
    raise SystemExit(main())
