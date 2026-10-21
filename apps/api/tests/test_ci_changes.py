import json
import subprocess

import pytest
from scripts import ci_changes


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README.md", {"docs"}),
        (
            "scripts/commerce-workload/requirements.txt",
            {"containers", "arm64", "integration", "workload"},
        ),
        ("docs/getting-started/quick-start.md", {"docs"}),
        ("apps/api/tests/test_auth.py", {"api"}),
        ("apps/web/tests/unit/overview.test.tsx", {"web"}),
        ("apps/web/src/app/globals.css", {"web", "containers"}),
        ("apps/web/public/brand/logo.png", {"web", "containers"}),
        ("apps/api/app/providers/postgres.py", {"api", "containers", "integration"}),
        ("apps/web/src/features/pipelines/wizard.tsx", {"web", "containers", "integration"}),
        ("deploy/kubernetes/api.yaml", {"deployment"}),
        ("package-lock.json", {"web", "audit_web", "containers", "arm64", "integration"}),
        ("apps/api/pyproject.toml", {"api", "audit_api", "containers", "arm64", "integration"}),
        (
            "scripts/commerce-workload/generate.py",
            {"api", "containers", "arm64", "integration", "workload"},
        ),
    ],
)
def test_changed_files_select_required_gates_without_unrelated_infrastructure(path, expected):
    selected = ci_changes.classify([path])
    assert {gate for gate, enabled in selected.items() if enabled} == expected


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "new-build-system.config"])
def test_ci_changes_and_unknown_build_configuration_fail_closed(path):
    assert all(ci_changes.classify([path]).values())


def test_manual_fast_mode_and_full_mode_are_explicit():
    assert ci_changes.select_event("workflow_dispatch", {})["docs"]
    assert not ci_changes.select_event("workflow_dispatch", {})["containers"]
    assert all(ci_changes.select_event("workflow_dispatch", {}, full=True).values())
    assert all(ci_changes.select_event("push", {"before": "0" * 40}).values())


def test_weekly_audit_does_not_start_application_or_fixture_containers():
    selected = ci_changes.select_event("schedule", {})
    assert {gate for gate, enabled in selected.items() if enabled} == {"audit_web", "audit_api"}


def test_github_output_contract_uses_boolean_strings(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({}))
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setenv("RUN_FULL_CI", "false")
    ci_changes.main()
    values = dict(line.split("=") for line in output.read_text().splitlines())
    assert values["docs"] == "true"
    assert values["containers"] == "false"
    assert all(values[gate] in {"true", "false"} for gate in ci_changes.GATES)
    assert json.loads(values["container_matrix"]) == {
        "include": [{"runner": "ubuntu-24.04", "arch": "amd64"}]
    }


@pytest.mark.parametrize(
    "path",
    [
        "compose.yaml",
        "compose.test.yaml",
        ".env.example",
        ".dockerignore",
        "infrastructure/docker/api.Dockerfile",
        "apps/web/Dockerfile",
        "apps/web/package.json",
        "packages/contracts/package.json",
        "package-lock.json",
        "apps/api/pyproject.toml",
        "scripts/commerce-workload/requirements.txt",
        "infrastructure/connect/secret-provider/Provider.java",
        "apps/api/app/native/module.c",
    ],
)
def test_architecture_sensitive_pr_changes_keep_native_arm64(path):
    selected = ci_changes.classify([path])
    assert selected["containers"] and selected["arm64"]
    assert ci_changes.container_matrix(selected)["include"] == [
        {"runner": "ubuntu-24.04", "arch": "amd64"},
        {"runner": "ubuntu-24.04-arm", "arch": "arm64"},
    ]


@pytest.mark.parametrize(
    "path",
    ["apps/web/src/app/globals.css", "apps/web/src/app/page.tsx", "apps/api/app/main.py"],
)
@pytest.mark.parametrize("event_name", ["pull_request", "push"])
def test_application_prs_use_amd64_but_default_branch_keeps_arm64(path, event_name, monkeypatch):
    def diff(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=path.encode() + b"\0")

    monkeypatch.setattr(ci_changes.subprocess, "run", diff)
    event = {"before": "a" * 40, "pull_request": {"base": {"sha": "a" * 40}}}
    selected = ci_changes.select_event(event_name, event)
    assert selected["containers"]
    assert selected["arm64"] is (event_name == "push")
    assert len(ci_changes.container_matrix(selected)["include"]) == (
        2 if event_name == "push" else 1
    )


def test_mixed_pr_keeps_arm64_when_any_file_changes_native_packaging():
    selected = ci_changes.classify(["apps/web/src/app/globals.css", "apps/api/pyproject.toml"])
    assert selected["arm64"] and selected["integration"]


def test_full_and_fail_closed_modes_always_include_both_native_runners():
    for selected in (
        ci_changes.select_event("workflow_dispatch", {}, full=True),
        ci_changes.select_event("pull_request", {}),
        ci_changes.classify([".github/workflows/ci.yml"]),
        ci_changes.classify(["new-build-system.config"]),
    ):
        assert len(ci_changes.container_matrix(selected)["include"]) == 2


def test_unavailable_git_base_runs_all_checks(monkeypatch):
    def unavailable(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(ci_changes.subprocess, "run", unavailable)
    assert all(ci_changes.select_event("push", {"before": "a" * 40}).values())


def test_deleted_runtime_file_still_selects_runtime_tests(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "--quiet")
    git("config", "user.name", "CI test")
    git("config", "user.email", "ci@example.test")
    source = tmp_path / "apps/api/app/provider.py"
    source.parent.mkdir(parents=True)
    source.write_text("fixture = True\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "fixture")
    base = git("rev-parse", "HEAD")
    source.rename(tmp_path / "README.md")
    git("add", "--all")
    git("commit", "--quiet", "-m", "rename runtime to documentation")
    monkeypatch.setattr(ci_changes, "ROOT", tmp_path)
    selected = ci_changes.select_event("pull_request", {"pull_request": {"base": {"sha": base}}})
    assert selected["docs"] and selected["api"] and selected["integration"]
