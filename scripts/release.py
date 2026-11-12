"""Inspect a release candidate locally. Never fetch, tag, push or publish."""

from __future__ import annotations

import argparse
import json

from release_metadata import inspect_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan"])
    parser.add_argument("--tag", help="Expected tag; empty for an untagged candidate")
    parser.add_argument(
        "--require-final",
        action="store_true",
        help="Require finalized metadata, all configured acceptance gates and a clean checkout",
    )
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
        candidate = inspect_candidate(tag=args.tag, require_final=args.require_final)
    except (ValueError, OSError, RuntimeError) as error:
        if args.json:
            print(json.dumps({"status": "BLOCKED", "error": str(error)}, indent=2))
        parser.exit(1, f"{error}\n")
    if args.json:
        print(json.dumps(candidate, indent=2))
    else:
        print(f"Candidate: {candidate['tag']} at {candidate['git_sha']}")
        print(
            f"Branch: {candidate['branch'] or '(detached)'}; dirty: {candidate['dirty']}"
        )
        print(f"Notes: {candidate['notes']}")
        print(f"Metadata: {'FINAL' if candidate['metadata_final'] else 'DRAFT'}")
        print(f"Acceptance: {candidate['acceptance']['status']}")
        for blocker in candidate["acceptance"]["blockers"]:
            print(f"BLOCKED: {blocker}")
        for image in candidate["images"]:
            print(f"Planned image (not published): {image}")
        for warning in candidate["warnings"]:
            print(f"WARNING: {warning}")
        print(candidate["remote_state"])
        print(candidate["release_approval"])
    return int(args.require_new_tag and not candidate["new_local_tag_available"])


if __name__ == "__main__":
    raise SystemExit(main())
