"""Release preparation must be version-independent, read-only and fail closed."""

import importlib
import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def metadata(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("release_metadata")


@pytest.fixture
def release_tree(tmp_path):
    files = {
        "apps/api/pyproject.toml": '[project]\nversion = "1.2.3"\n',
        "apps/api/uv.lock": '[[package]]\nname = "cluecdc-api"\nversion = "1.2.3"\n',
        "CHANGELOG.md": "# Changelog\n\n## [1.2.3] - 2026-10-15\n",
        "docs/releases/v1.2.3.md": "# ClueCDC v1.2.3\n\nReviewed release notes.\n",
    }
    for name, component in (
        ("api", "api"),
        ("worker", "api"),
        ("web", "web"),
        ("connect", "connect"),
    ):
        files[f"deploy/kubernetes/{name}.yaml"] = (
            f"image: ghcr.io/cluedata/cluecdc-{component}:1.2.3\n"
        )
    for name, contents in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("version", ["0.0.0", "1.2.3", "1.2.3-rc.1", "1.2.3-0", "1.2.3-alpha-1"])
def test_semver_accepts_valid_versions(metadata, version):
    assert metadata.SEMVER.fullmatch(version)


@pytest.mark.parametrize(
    "version", ["01.2.3", "1.02.3", "1.2", "1.2.3-01", "1.2.3-..", "1.2.3-", "v1.2.3"]
)
def test_semver_rejects_invalid_versions(metadata, version):
    assert not metadata.SEMVER.fullmatch(version)


def test_metadata_checks_every_release_reference(metadata, release_tree):
    result = metadata.validate_metadata(release_tree, tag="v1.2.3", require_final=True)
    assert result["version"] == "1.2.3"
    assert result["warnings"] == []
    assert result["notes"] == "docs/releases/v1.2.3.md"
    assert result["images"] == [
        f"ghcr.io/cluedata/cluecdc-{name}:1.2.3" for name in ("web", "api", "connect")
    ]
    with pytest.raises(ValueError, match="does not match"):
        metadata.validate_metadata(release_tree, tag="v1.2.4")


def test_metadata_accepts_release_name_suffix(metadata, release_tree):
    (release_tree / "docs/releases/v1.2.3.md").write_text(
        "# ClueCDC v1.2.3 — Operator Experience\n\nReviewed release notes.\n",
        encoding="utf-8",
    )
    assert metadata.validate_metadata(release_tree, require_final=True)["warnings"] == []


@pytest.mark.parametrize(
    "name,contents",
    [
        ("CHANGELOG.md", "Text mentioning ## [1.2.3] - 2026-10-15\n"),
        ("CHANGELOG.md", "## [1.2.3] - 2026-10-15\n## [1.2.3] - Unreleased\n"),
        ("apps/api/uv.lock", '[[package]]\nname = "cluecdc-api"\nversion = "1.2.2"\n'),
        ("docs/releases/v1.2.3.md", "# ClueCDC v1.2.4\n"),
        ("deploy/kubernetes/worker.yaml", "image: ghcr.io/cluedata/cluecdc-api:1.2.2\n"),
        (
            "deploy/kubernetes/api.yaml",
            "image: ghcr.io/cluedata/cluecdc-api:1.2.3\n"
            "image: ghcr.io/cluedata/cluecdc-api:1.2.2\n",
        ),
        (
            "deploy/kubernetes/api.yaml",
            "image: ghcr.io/cluedata/cluecdc-api:1.2.3\nimage: ghcr.io/other/cluecdc-api:1.2.3\n",
        ),
    ],
)
def test_metadata_rejects_drift(metadata, release_tree, name, contents):
    (release_tree / name).write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        metadata.validate_metadata(release_tree)


@pytest.mark.parametrize("release_date", ["Unreleased", "2026-02-30", "20261015"])
def test_draft_metadata_is_allowed_only_for_preparation(metadata, release_tree, release_date):
    (release_tree / "CHANGELOG.md").write_text(f"## [1.2.3] - {release_date}\n", encoding="utf-8")
    (release_tree / "docs/releases/v1.2.3.md").write_text(
        "# ClueCDC v1.2.3\n\nUnreleased draft.\n", encoding="utf-8"
    )
    assert len(metadata.validate_metadata(release_tree)["warnings"]) == 2
    with pytest.raises(ValueError, match="not final"):
        metadata.validate_metadata(release_tree, require_final=True)


def configured_acceptance(root, status="PASS", *, advisory=False):
    project = root / "apps/api/pyproject.toml"
    project.write_text(
        project.read_text() + "\n[tool.cluecdc.release]\n"
        'acceptance_matrix = "acceptance.json"\n'
        + (
            'required_acceptance_gates = ["G01"]\nadvisory_acceptance_gates = ["G13"]\n'
            if advisory
            else 'required_acceptance_gates = ["G01", "G13"]\n'
        )
    )
    records = [
        {"gate_id": "G01", "status": "PASS", "evidence_path": ["evidence/cdc.json"]},
        {
            "gate_id": "G13",
            "status": status,
            "evidence_path": ["evidence/notification.json"],
            "failure_reason": "Real authorized HTTPS delivery receipts missing"
            if status != "PASS"
            else None,
        },
    ]
    (root / "acceptance.json").write_text(json.dumps(records))
    return records


def test_finalized_metadata_and_completed_acceptance_are_distinct(metadata, release_tree):
    configured_acceptance(release_tree, "BLOCKED")
    result = metadata.validate_metadata(release_tree, require_final=True)
    assert result["metadata_final"] and result["warnings"] == []
    assert result["acceptance"]["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="G13: BLOCKED.*receipts missing"):
        metadata.validate_metadata(release_tree, require_final=True, require_acceptance=True)
    with pytest.raises(ValueError, match="G13: BLOCKED"):
        metadata.inspect_candidate(release_tree, require_final=True)


def test_complete_acceptance_can_pass_without_rewriting_evidence(metadata, release_tree):
    configured_acceptance(release_tree)
    before = (release_tree / "acceptance.json").read_bytes()
    result = metadata.validate_metadata(release_tree, require_final=True, require_acceptance=True)
    assert result["acceptance"]["status"] == "PASS"
    assert result["acceptance"]["blockers"] == []
    assert (release_tree / "acceptance.json").read_bytes() == before


@pytest.mark.parametrize("status", ["FAIL", "BLOCKED", "NOT_RUN"])
def test_no_nonpassing_acceptance_state_can_publish(metadata, release_tree, status):
    configured_acceptance(release_tree, status)
    with pytest.raises(ValueError, match=f"G13: {status}"):
        metadata.validate_metadata(release_tree, require_acceptance=True)


@pytest.mark.parametrize("status", ["PASS", "FAIL", "BLOCKED", "NOT_RUN"])
def test_advisory_acceptance_is_reported_without_blocking(metadata, release_tree, status):
    configured_acceptance(release_tree, status, advisory=True)
    matrix = release_tree / "acceptance.json"
    before = matrix.read_bytes()
    result = metadata.validate_metadata(release_tree, require_final=True, require_acceptance=True)
    acceptance = result["acceptance"]
    assert acceptance["status"] == "PASS" and acceptance["blockers"] == []
    assert acceptance["gates"] == [{"gate_id": "G01", "status": "PASS"}]
    assert acceptance["advisory"][0]["gate_id"] == "G13"
    assert acceptance["advisory"][0]["status"] == status
    assert matrix.read_bytes() == before


def test_advisory_exception_does_not_disable_required_acceptance(metadata, release_tree):
    records = configured_acceptance(release_tree, "BLOCKED", advisory=True)
    records[0]["status"] = "FAIL"
    (release_tree / "acceptance.json").write_text(json.dumps(records))
    with pytest.raises(ValueError, match="G01: FAIL"):
        metadata.validate_metadata(release_tree, require_acceptance=True)


@pytest.mark.parametrize("advisory", [None, "G13", [""], [13], ["G13", "G13"], ["G01"]])
def test_invalid_advisory_configuration_is_rejected(metadata, release_tree, advisory):
    configured_acceptance(release_tree, "BLOCKED", advisory=True)
    with pytest.raises(ValueError, match="Advisory acceptance gates"):
        metadata.acceptance_status(
            release_tree,
            {
                "acceptance_matrix": "acceptance.json",
                "required_acceptance_gates": ["G01"],
                "advisory_acceptance_gates": advisory,
            },
        )


def test_advisory_record_must_still_exist(metadata, release_tree):
    records = configured_acceptance(release_tree, "BLOCKED", advisory=True)
    (release_tree / "acceptance.json").write_text(json.dumps(records[:1]))
    with pytest.raises(ValueError, match="missing=\\['G13'\\]"):
        metadata.validate_metadata(release_tree, require_acceptance=True)


@pytest.mark.parametrize(
    "problem",
    ["missing", "duplicate", "unexpected", "unknown_status", "no_evidence", "invalid_list"],
)
def test_malformed_or_incomplete_acceptance_is_rejected(metadata, release_tree, problem):
    records = configured_acceptance(release_tree)
    if problem == "missing":
        records.pop()
    elif problem == "duplicate":
        records.append(records[0])
    elif problem == "unexpected":
        records.append({**records[0], "gate_id": "G99"})
    elif problem == "unknown_status":
        records[1]["status"] = "SKIPPED"
    elif problem == "no_evidence":
        records[1]["evidence_path"] = []
    else:
        records = {"G13": "PASS"}
    (release_tree / "acceptance.json").write_text(json.dumps(records))
    with pytest.raises(ValueError):
        metadata.validate_metadata(release_tree)


def test_configured_matrix_cannot_disappear_or_escape_repository(metadata, release_tree):
    configured_acceptance(release_tree)
    (release_tree / "acceptance.json").unlink()
    with pytest.raises(FileNotFoundError):
        metadata.validate_metadata(release_tree)
    with pytest.raises(ValueError, match="inside the repository"):
        metadata.acceptance_status(
            release_tree,
            {"acceptance_matrix": "../outside.json", "required_acceptance_gates": ["G13"]},
        )


def test_legacy_releases_without_configured_gates_remain_valid(metadata, release_tree):
    result = metadata.validate_metadata(release_tree, require_final=True, require_acceptance=True)
    assert result["acceptance"]["status"] == "NOT_REQUIRED"


@pytest.mark.parametrize("configuration", [False, "", [], None])
def test_invalid_configuration_cannot_disable_acceptance(metadata, release_tree, configuration):
    with pytest.raises(ValueError, match="must be a table"):
        metadata.acceptance_status(release_tree, configuration)


def test_publication_rechecks_acceptance_before_network(publication, release_tree, monkeypatch):
    configured_acceptance(release_tree, "BLOCKED")
    monkeypatch.setattr(
        publication,
        "validate_metadata",
        lambda **kwargs: importlib.import_module("release_metadata").validate_metadata(
            release_tree, **kwargs
        ),
    )
    monkeypatch.setattr(
        publication,
        "verify_available",
        lambda *args: pytest.fail("No registry request while G13 is blocked"),
    )
    monkeypatch.setattr(sys, "argv", ["check_release_publication.py", "--tag", "v1.2.3"])
    with pytest.raises(SystemExit) as error:
        publication.main()
    assert error.value.code == 1


def test_publication_preflight_proceeds_with_blocked_advisory(
    metadata, publication, release_tree, monkeypatch
):
    configured_acceptance(release_tree, "BLOCKED", advisory=True)
    monkeypatch.setattr(
        publication,
        "validate_metadata",
        lambda **kwargs: metadata.validate_metadata(release_tree, **kwargs),
    )
    calls = []
    monkeypatch.setattr(publication, "verify_available", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["check_release_publication.py", "--tag", "v1.2.3"])
    assert publication.main() == 0
    assert len(calls) == 1 and calls[0][1:3] == ("v1.2.3", "1.2.3")


def test_strict_plan_reports_json_blocker_before_git_access(
    metadata, release_tree, monkeypatch, capsys
):
    configured_acceptance(release_tree, "BLOCKED")
    spec = importlib.util.spec_from_file_location(
        "strict_release_plan_test", ROOT / "scripts/release.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(
        cli,
        "inspect_candidate",
        lambda **kwargs: metadata.inspect_candidate(release_tree, **kwargs),
    )
    monkeypatch.setattr(
        metadata,
        "git_output",
        lambda *args: pytest.fail("Acceptance must fail before Git inspection"),
    )
    monkeypatch.setattr(
        sys, "argv", ["release.py", "plan", "--tag", "v1.2.3", "--require-final", "--json"]
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED" and "G13: BLOCKED" in result["error"]


@pytest.mark.parametrize("tag_exists,dirty", [(False, False), (True, False), (True, True)])
def test_plan_only_runs_read_only_git_commands(
    metadata, release_tree, monkeypatch, tag_exists, dirty
):
    calls = []

    def output(command, **kwargs):
        calls.append(command)
        return {
            ("rev-parse", "HEAD"): "candidate-sha",
            ("rev-parse", "refs/tags/v1.2.3^{commit}"): "old-tag-sha",
            ("status", "--porcelain"): " M README.md" if dirty else "",
            ("branch", "--show-current"): "release/any-version",
        }[tuple(command[1:])]

    def run(command, **kwargs):
        calls.append(command)
        assert command == ["git", "show-ref", "--verify", "--quiet", "refs/tags/v1.2.3"]
        return SimpleNamespace(returncode=0 if tag_exists else 1)

    monkeypatch.setattr(metadata.subprocess, "check_output", output)
    monkeypatch.setattr(metadata.subprocess, "run", run)
    result = metadata.inspect_candidate(release_tree)
    assert result["git_sha"] == "candidate-sha"
    assert result["new_local_tag_available"] is not tag_exists
    assert result["local_tag_target"] == ("old-tag-sha" if tag_exists else None)
    assert result["dirty"] is dirty
    assert all(command[1] in {"rev-parse", "show-ref", "status", "branch"} for command in calls)
    assert result["release_approval"].startswith("NOT_GRANTED")


def test_git_failure_is_not_treated_as_an_available_tag(metadata, release_tree, monkeypatch):
    monkeypatch.setattr(metadata, "git_output", lambda *args: "sha")
    monkeypatch.setattr(
        metadata.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=128)
    )
    with pytest.raises(RuntimeError, match="Could not inspect"):
        metadata.inspect_candidate(release_tree)


def test_strict_plan_refuses_dirty_source_after_acceptance_passes(
    metadata, release_tree, monkeypatch
):
    configured_acceptance(release_tree)
    monkeypatch.setattr(
        metadata.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    monkeypatch.setattr(
        metadata,
        "git_output",
        lambda root, *args: " M changed.py" if args[0] == "status" else "fixture-sha",
    )
    with pytest.raises(ValueError, match="uncommitted changes"):
        metadata.inspect_candidate(release_tree, require_final=True)


def test_current_tag_is_expected_in_publication_validation(metadata, release_tree, monkeypatch):
    configured_acceptance(release_tree)
    monkeypatch.setattr(
        metadata.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(
        metadata,
        "git_output",
        lambda root, *args: "" if args[0] in {"status", "branch"} else "fixture-sha",
    )
    result = metadata.inspect_candidate(release_tree, tag="v1.2.3", require_final=True)
    assert result["warnings"] == []
    assert result["local_tag_target"] == result["git_sha"]
    assert result["new_local_tag_available"] is False


def test_repository_release_configuration_reports_g13_as_advisory(metadata):
    project = tomllib.loads((ROOT / "apps/api/pyproject.toml").read_text())
    settings = project["tool"]["cluecdc"]["release"]
    assert settings["required_acceptance_gates"] == [f"G{i:02d}" for i in range(1, 19) if i != 13]
    assert settings["advisory_acceptance_gates"] == ["G13"]
    matrix = json.loads((ROOT / settings["acceptance_matrix"]).read_text())
    assert {row["gate_id"] for row in matrix} == set(settings["required_acceptance_gates"]) | {
        "G13"
    }
    result = metadata.validate_metadata(
        ROOT, tag="v0.3.5", require_final=True, require_acceptance=True
    )
    assert result["acceptance"]["status"] == "PASS"
    assert result["acceptance"]["advisory"][0]["status"] == "BLOCKED"


@pytest.mark.parametrize(
    "require_new,available,exit_code", [(False, False, 0), (True, False, 1), (True, True, 0)]
)
def test_plan_json_and_strict_tag_availability_exit_code(
    metadata, monkeypatch, capsys, require_new, available, exit_code
):
    spec = importlib.util.spec_from_file_location("release_plan_test", ROOT / "scripts/release.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    candidate = {"new_local_tag_available": available, "git_sha": "fixture-sha"}
    monkeypatch.setattr(cli, "inspect_candidate", lambda **kwargs: candidate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["release.py", "plan", "--json", *(["--require-new-tag"] if require_new else [])],
    )
    assert cli.main() == exit_code
    assert json.loads(capsys.readouterr().out) == candidate


def load_gate(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    monkeypatch.setenv("WORKER_TEST_DATABASE_URL", "postgresql+asyncpg://fixture/release_test")
    spec = importlib.util.spec_from_file_location(
        "release_check_test", ROOT / "scripts/release-check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("branch", ["release/1.2.3", "fix/hardening", ""])
def test_shared_quality_profile_works_on_any_candidate_and_reports_omitted_gates(
    monkeypatch, capsys, branch
):
    gate = load_gate(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["release-check.py", "--preparation", "--quality-only"])
    monkeypatch.setattr(gate, "output", lambda *args: branch if "branch" in args else "")
    monkeypatch.setattr(gate.shutil, "which", lambda name: name)
    commands = []
    monkeypatch.setattr(gate, "run", lambda *command: commands.append(command))
    assert gate.main() == 0
    assert ("npm", "run", "test:web") in commands
    assert (sys.executable, "-m", "pytest", "apps/api/tests", "-q") in commands
    assert ("npm", "audit", "--omit=dev", "--audit-level=high") in commands
    assert ("npm", "audit", "--audit-level=critical") in commands
    assert (sys.executable, "-m", "pip_audit", "--local", "--skip-editable") in commands
    assert not any("scripts/hardening_acceptance.py" in command for command in commands)
    assert not any(command[:2] == ("docker", "build") for command in commands)
    assert "Not release approval" in capsys.readouterr().out


@pytest.mark.parametrize(
    "option", ["--skip-live", "--skip-docker", "--quality-only", "--allow-dirty"]
)
def test_release_mode_never_silently_omits_required_checks(monkeypatch, option):
    gate = load_gate(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["release-check.py", option])
    monkeypatch.setattr(gate, "output", lambda *args: "main" if "branch" in args else "")
    monkeypatch.setattr(gate, "run", lambda *args: pytest.fail("Checks must not start"))
    with pytest.raises(SystemExit, match="requires --preparation"):
        gate.main()


def test_quality_failure_stops_before_builds_or_live_checks(monkeypatch):
    gate = load_gate(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["release-check.py", "--preparation", "--quality-only"])
    monkeypatch.setattr(gate, "output", lambda *args: "main" if "branch" in args else "")
    monkeypatch.setattr(gate.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        gate, "run", lambda *args: (_ for _ in ()).throw(subprocess.CalledProcessError(1, args))
    )
    with pytest.raises(subprocess.CalledProcessError):
        gate.main()


def test_release_gate_refuses_missing_postgres_coverage(monkeypatch):
    gate = load_gate(monkeypatch)
    monkeypatch.delenv("WORKER_TEST_DATABASE_URL")
    monkeypatch.setattr(sys, "argv", ["release-check.py", "--preparation", "--quality-only"])
    monkeypatch.setattr(gate, "output", lambda *args: "main" if "branch" in args else "")
    monkeypatch.setattr(gate, "run", lambda *args: pytest.fail("Checks must not start"))
    with pytest.raises(SystemExit, match="disposable PostgreSQL"):
        gate.main()


def test_local_live_profile_forwards_explicit_browser_and_fixture_options(monkeypatch):
    gate = load_gate(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release-check.py",
            "--preparation",
            "--skip-docker",
            "--browser",
            "--live-env-file",
            "disposable.env",
        ],
    )
    monkeypatch.setattr(gate, "output", lambda *args: "fix/candidate" if "branch" in args else "")
    monkeypatch.setattr(gate.shutil, "which", lambda name: name)
    commands = []
    monkeypatch.setattr(gate, "run", lambda *command: commands.append(command))
    assert gate.main() == 0
    assert (
        sys.executable,
        "scripts/hardening_acceptance.py",
        "--browser",
        "--env-file",
        "disposable.env",
    ) in commands


@pytest.fixture
def publication(metadata):
    return importlib.import_module("check_release_publication")


@pytest.mark.parametrize("existing", ["release", "web", "api", "connect", None])
def test_publication_checks_all_images_and_never_changes_remote_state(
    publication, monkeypatch, existing
):
    calls = []

    def request(url, *, headers, method="GET", allow_missing=False):
        calls.append((url, method))
        assert method in {"GET", "HEAD"}
        if "api.github.com" in url:
            assert allow_missing and method == "GET"
            return b"{}" if existing == "release" else None
        if "/token?" in url:
            assert not allow_missing and headers["Authorization"].startswith("Basic ")
            return b'{"token":"fixture-bearer-not-a-secret"}'
        assert method == "HEAD" and allow_missing
        assert headers["Authorization"] == "Bearer fixture-bearer-not-a-secret"
        return b"" if f"cluecdc-{existing}/" in url else None

    monkeypatch.setattr(publication, "request", request)
    if existing:
        with pytest.raises(RuntimeError, match="already exists"):
            publication.verify_available("Owner/Repo", "v1.2.3", "1.2.3", "actor", "fixture-token")
    else:
        publication.verify_available("Owner/Repo", "v1.2.3", "1.2.3", "actor", "fixture-token")
        assert len(calls) == 7  # One release, three auth tokens and three manifest HEADs.
    if existing == "release":
        assert len(calls) == 1


@pytest.mark.parametrize(
    "status,missing",
    [(404, True), (404, False), (401, True), (403, True), (429, True), (500, True)],
)
def test_publication_only_treats_explicit_not_found_as_missing(
    publication, monkeypatch, status, missing
):
    def urlopen(*args, **kwargs):
        raise HTTPError("https://ghcr.io/fixture", status, "error", {}, None)

    monkeypatch.setattr(publication, "urlopen", urlopen)
    if status == 404 and missing:
        assert (
            publication.request("https://ghcr.io/fixture", headers={}, allow_missing=missing)
            is None
        )
    else:
        with pytest.raises(RuntimeError, match=f"HTTP {status}"):
            publication.request("https://ghcr.io/fixture", headers={}, allow_missing=missing)


def test_publication_network_failure_does_not_allow_publishing(publication, monkeypatch):
    def urlopen(*args, **kwargs):
        raise URLError("fixture-network-unavailable")

    monkeypatch.setattr(publication, "urlopen", urlopen)
    with pytest.raises(URLError):
        publication.request("https://ghcr.io/fixture", headers={}, allow_missing=True)


def test_each_matrix_retry_rechecks_only_its_own_image(publication, monkeypatch):
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        return b'{"token":"fixture-bearer"}' if "/token?" in url else None

    monkeypatch.setattr(publication, "request", request)
    publication.verify_available(
        "owner/repo", "v1.2.3", "1.2.3", "actor", "fixture-token", ("api",)
    )
    assert len(calls) == 3
    assert calls[-1] == "https://ghcr.io/v2/owner/cluecdc-api/manifests/1.2.3"
    assert not any("cluecdc-web" in url or "cluecdc-connect" in url for url in calls)


@pytest.mark.parametrize(
    "repository,actor,token",
    [("bad/repo/path", "actor", "token"), ("owner/repo", "", "token"), ("owner/repo", "actor", "")],
)
def test_publication_rejects_invalid_configuration_before_network(
    publication, monkeypatch, repository, actor, token
):
    monkeypatch.setattr(
        publication, "request", lambda *args, **kwargs: pytest.fail("No network allowed")
    )
    with pytest.raises(ValueError):
        publication.verify_available(repository, "v1.2.3", "1.2.3", actor, token)


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def test_candidate_and_publication_share_api_validation_without_live_gates():
    candidate = workflow("release-candidate.yml")
    release = workflow("release.yml")
    assert candidate["jobs"]["validate"]["uses"] == release["jobs"]["validate"]["uses"]
    for configuration in (candidate, release):
        assert not {"hardening", "data-quality"} & configuration["jobs"].keys()
    assert candidate["permissions"] == {"contents": "read"}
    assert set(candidate["on"]) == {"workflow_dispatch"}
    assert not any("permissions" in job for job in candidate["jobs"].values())
    assert release["jobs"]["validate"]["with"]["require-final"] == "true"
    assert release["jobs"]["validate"]["with"]["tag"] == "${{ github.ref_name }}"


def test_publication_requires_api_validation_and_preflight_without_overwriting_assets():
    release = workflow("release.yml")
    assert set(release["on"]) == {"push"}
    assert release["on"]["push"]["tags"] == ["v*"]
    assert set(release["jobs"]) == {"validate", "containers", "github-release"}
    assert release["jobs"]["containers"]["needs"] == "validate"
    assert set(release["jobs"]["github-release"]["needs"]) == {"validate", "containers"}
    assert release["jobs"]["validate"]["permissions"] == {"contents": "read", "packages": "read"}
    for name in ("containers", "github-release"):
        assert "if" not in release["jobs"][name]
    commands = "\n".join(step.get("run", "") for step in release["jobs"]["github-release"]["steps"])
    assert "gh release create" in commands and "--verify-tag" in commands
    assert "--clobber" not in commands and "gh release edit" not in commands
    assert "--prerelease" in commands
    assert "sha256sum --check --strict" in commands
    assert "metadata['git_sha'] != os.environ['GITHUB_SHA']" in commands
    containers = release["jobs"]["containers"]["steps"]
    assert any("--component" in step.get("run", "") for step in containers)


def test_data_quality_stays_available_manually_without_blocking_candidates():
    candidate = workflow("release-candidate.yml")
    assert set(candidate["jobs"]) == {"validate"}
    data_quality = workflow("data-quality.yml")
    assert set(data_quality["on"]) == {"workflow_call", "workflow_dispatch"}
    assert data_quality["permissions"] == {"contents": "read"}
    # Reusable and standalone runs must not cancel one another on the same ref.
    assert "github.workflow" in data_quality["concurrency"]["group"]
    job = data_quality["jobs"]["verify"]
    assert job["env"]["DQ_LIVE_TESTS"] == "1"
    assert job["env"]["DQ_BROWSER_TESTS"] == "1"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "scripts/data_quality_migrations.py" in commands
    assert "tests/e2e/data-quality.spec.ts" in commands
    assert any(
        step.get("uses", "").startswith("actions/upload-artifact@")
        and step["with"]["name"] == "data-quality-evidence"
        and step["if"] == "always()"
        for step in job["steps"]
    )


def test_shared_validation_requires_api_tests_and_preserves_source_artifact_identity():
    shared = workflow("release-validation.yml")["jobs"]
    assert set(shared) == {"quality"}  # No nested CI selector or skipped component jobs.
    validation = shared["quality"]
    assert validation["env"]["WORKER_TEST_DATABASE_URL"].startswith("postgresql+asyncpg://")
    assert validation["services"]["worker-test-postgres"]["image"] == "postgres:17.9"
    steps = validation["steps"]
    names = [step.get("name") for step in steps]
    metadata = names.index("Validate metadata and acceptance before installing test dependencies")
    preflight = names.index("Publication preflight before tests or builds")
    install = names.index("Install API test dependencies")
    tests = names.index("Test backend APIs, migrations and native PostgreSQL worker contracts")
    assert metadata < preflight < install < tests
    assert steps[preflight]["if"] == "inputs.require-final"
    assert "git merge-base --is-ancestor" in steps[preflight]["run"]
    assert "check_release_publication.py" in steps[preflight]["run"]
    assert "pytest apps/api/tests" in steps[tests]["run"]
    assert "--junitxml=artifacts/api-tests.xml" in steps[tests]["run"]
    commands = "\n".join(step.get("run", "") for step in validation["steps"])
    assert commands.count("python scripts/release.py") == 1
    assert "--require-final" in commands
    assert "scripts/check-repository.py" in commands
    assert "release-check.py" not in commands and "docker" not in commands
    assert "git archive" in commands and "SHA256SUMS" in commands
    assert "release-metadata.json" in commands
    reports = [s for s in steps if s.get("with", {}).get("name") == "api-tests"]
    assert len(reports) == 1 and reports[0]["if"] == "always()"
    assert not any("setup-node" in step.get("uses", "") for step in validation["steps"])
    hardening = workflow("hardening.yml")["jobs"]
    assert {"live-hardening", "kubernetes-hardening"} <= set(hardening)
    assert any("--browser" in step.get("run", "") for step in hardening["live-hardening"]["steps"])
