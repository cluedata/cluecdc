"""Reject unresolved Git conflicts before dependency installation or image builds."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def checker(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[3] / "scripts/check-repository.py"
    spec = importlib.util.spec_from_file_location("check_repository", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


@pytest.mark.parametrize("marker", ["<" * 7, ">" * 7])
@pytest.mark.parametrize("filename", ["pyproject.toml", "uv.lock"])
def test_repository_check_rejects_conflicts_in_dependency_files(
    checker, tmp_path, filename, marker, capsys
):
    manifest = tmp_path / filename
    manifest.write_text(f'[project]\n{marker} branch\nname = "fixture"\n', encoding="utf-8")
    checker.tracked_files = lambda: [manifest]

    assert checker.main() == 1
    assert f"{filename}: unresolved merge conflict" in capsys.readouterr().out


def test_repository_check_accepts_markdown_underlines_and_inline_marker_examples(checker, tmp_path):
    document = tmp_path / "README.md"
    document.write_text("Heading\n=======\nExample: " + "<" * 7 + " HEAD\n", encoding="utf-8")
    checker.tracked_files = lambda: [document]

    assert checker.main() == 0
