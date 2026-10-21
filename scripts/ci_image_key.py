"""Invalidate a fixture image cache when any upstream base image changes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def base_images(dockerfile: str) -> list[str]:
    stages: set[str] = set()
    images: list[str] = []
    for line in dockerfile.splitlines():
        tokens = line.split()
        if not tokens or tokens[0].upper() != "FROM":
            continue
        arguments = [token for token in tokens[1:] if not token.startswith("--")]
        reference = arguments[0]
        if "$" in reference:
            raise ValueError(
                "Resolve Dockerfile ARG references before caching a fixture"
            )
        if reference.lower() not in stages and reference != "scratch":
            images.append(reference)
        if len(arguments) >= 3 and arguments[1].upper() == "AS":
            stages.add(arguments[2].lower())
    if not images:
        raise ValueError("No upstream base images found")
    return list(dict.fromkeys(images))


def cache_digest(dockerfile: str) -> str:
    digests = []
    for image in base_images(dockerfile):
        result = subprocess.run(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                "--format",
                "{{json .Manifest.Digest}}",
                image,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        digest = json.loads(result.stdout)
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
        ):
            raise ValueError(f"Cannot determine immutable base digest for {image}")
        digests.append(f"{image}@{digest}")
    return hashlib.sha256("\n".join(digests).encode()).hexdigest()


def main() -> None:
    digest = cache_digest(Path(sys.argv[1]).read_text(encoding="utf-8"))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"digest={digest}\n")


if __name__ == "__main__":
    main()
