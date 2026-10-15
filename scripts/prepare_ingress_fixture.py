"""TEST-01: retain admission Jobs until the disposable cluster is deleted."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

ADMISSION_JOBS = {"ingress-nginx-admission-create", "ingress-nginx-admission-patch"}


def prepare(manifest: str) -> str:
    documents = list(yaml.safe_load_all(manifest))
    jobs = [
        document
        for document in documents
        if document
        and document.get("kind") == "Job"
        and document.get("metadata", {}).get("namespace") == "ingress-nginx"
        and document.get("metadata", {}).get("name") in ADMISSION_JOBS
    ]
    if len(jobs) != 2 or {job["metadata"]["name"] for job in jobs} != ADMISSION_JOBS:
        raise ValueError("Expected both ingress admission Jobs in the verified fixture")
    for job in jobs:
        # Upstream TTL=0 races sequential kubectl waits. The caller deletes the cluster.
        job["spec"].pop("ttlSecondsAfterFinished", None)
    return yaml.safe_dump_all(documents, sort_keys=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(
        prepare(args.source.read_text(encoding="utf-8")), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
