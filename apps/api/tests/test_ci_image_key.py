import json
import subprocess

import pytest
from scripts import ci_image_key


def test_base_images_skip_stage_references_and_build_platform_flags():
    assert ci_image_key.base_images("""
        FROM quay.io/debezium/connect:3.3.1.Final AS upstream
        FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS downloads
        FROM --platform=$BUILDPLATFORM eclipse-temurin:21-jdk AS builder
        FROM upstream
    """) == [
        "quay.io/debezium/connect:3.3.1.Final",
        "debian:bookworm-slim",
        "eclipse-temurin:21-jdk",
    ]


@pytest.mark.parametrize("dockerfile", ["FROM $BASE", "# no base images", "FROM scratch"])
def test_unresolved_cache_inputs_fail_closed(dockerfile):
    with pytest.raises(ValueError):
        ci_image_key.base_images(dockerfile)


def test_changed_base_digest_invalidates_final_image_cache(monkeypatch):
    digest = "a" * 64

    def inspect(command, **kwargs):
        assert command[:4] == ["docker", "buildx", "imagetools", "inspect"]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps("sha256:" + digest))

    monkeypatch.setattr(ci_image_key.subprocess, "run", inspect)
    first = ci_image_key.cache_digest("FROM debian:bookworm-slim")
    digest = "b" * 64
    assert first != ci_image_key.cache_digest("FROM debian:bookworm-slim")


def test_invalid_registry_response_is_not_used_as_a_cache_key(monkeypatch):
    monkeypatch.setattr(
        ci_image_key.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="null"),
    )
    with pytest.raises(ValueError):
        ci_image_key.cache_digest("FROM debian:bookworm-slim")
