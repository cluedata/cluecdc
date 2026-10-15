"""Read-only publication preflight; refuse existing releases or immutable image tags."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from release_metadata import COMPONENTS, validate_metadata


def request(
    url: str, *, headers: dict, method: str = "GET", allow_missing: bool = False
) -> bytes | None:
    try:
        with urlopen(
            Request(url, headers=headers, method=method), timeout=30
        ) as response:
            return response.read()
    except HTTPError as error:
        if allow_missing and error.code == 404:
            return None
        # Never print registry bearer tokens, auth headers, or response payloads.
        raise RuntimeError(
            f"Publication preflight {method} failed with HTTP {error.code}"
        ) from None


def verify_available(
    repository: str,
    tag: str,
    version: str,
    actor: str,
    token: str,
    components: tuple[str, ...] = COMPONENTS,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected GitHub owner/repository")
    if not actor or not token:
        raise ValueError(
            "GITHUB_ACTOR and GH_TOKEN are required for publication preflight"
        )
    release = request(
        f"https://api.github.com/repos/{repository}/releases/tags/{tag}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        allow_missing=True,
    )
    if release is not None:
        raise RuntimeError(
            f"GitHub Release {tag} already exists; refusing to overwrite it"
        )
    owner = repository.split("/")[0].lower()
    basic = base64.b64encode(f"{actor}:{token}".encode()).decode()
    for component in components:
        image = f"{owner}/cluecdc-{component}"
        query = urlencode({"service": "ghcr.io", "scope": f"repository:{image}:pull"})
        response = request(
            f"https://ghcr.io/token?{query}",
            headers={"Authorization": f"Basic {basic}"},
        )
        bearer = json.loads(response)["token"]
        manifest = request(
            f"https://ghcr.io/v2/{image}/manifests/{version}",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Accept": ", ".join(
                    (
                        "application/vnd.oci.image.index.v1+json",
                        "application/vnd.oci.image.manifest.v1+json",
                        "application/vnd.docker.distribution.manifest.list.v2+json",
                        "application/vnd.docker.distribution.manifest.v2+json",
                    )
                ),
            },
            method="HEAD",
            allow_missing=True,
        )
        if manifest is not None:
            raise RuntimeError(
                f"ghcr.io/{image}:{version} already exists; immutable versions cannot be rebuilt"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--component",
        choices=COMPONENTS,
        help="Recheck one image immediately before pushing it",
    )
    args = parser.parse_args()
    try:
        metadata = validate_metadata(tag=args.tag, require_final=True)
        verify_available(
            os.environ.get("GITHUB_REPOSITORY", ""),
            metadata["tag"],
            metadata["version"],
            os.environ.get("GITHUB_ACTOR", ""),
            os.environ.get("GH_TOKEN", ""),
            (args.component,) if args.component else COMPONENTS,
        )
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        parser.exit(1, f"Publication blocked: {error}\n")
    print(
        "No existing release or immutable image tag found. No remote state was changed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
