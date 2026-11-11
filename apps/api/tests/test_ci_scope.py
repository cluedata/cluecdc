"""Path selection must not start tests for unrelated components."""

import json
import subprocess

import pytest
from scripts import ci_scope


@pytest.mark.parametrize(
    "paths,expected,images",
    [
        (["apps/api/app/services/data_quality.py"], {"api"}, []),
        (["apps/api/tests/test_auth.py"], {"api"}, []),
        (["apps/web/src/components/shell.tsx"], {"web"}, []),
        (["apps/web/tests/e2e/users.spec.ts"], {"web"}, []),
        (["docs/operations/configuration.md"], {"docs"}, []),
        (["README.md", "mkdocs.yml"], {"docs"}, []),
        (["apps/api/AGENTS.md"], {"docs"}, []),
        (["packages/ui/src/button.tsx"], {"web"}, []),
        (["packages/contracts/src/index.ts"], {"api", "web"}, []),
        (["package-lock.json"], {"web"}, []),
        (["apps/api/pyproject.toml"], {"api"}, []),
        (["deploy/kubernetes/api.yaml"], {"deployment"}, []),
        (["scripts/deployment_validation.py"], {"deployment"}, []),
        (["compose.test.yaml", ".env.example"], {"compose"}, []),
        (["infrastructure/tests/data-quality.compose.yaml"], {"compose"}, []),
        ([".github/workflows/ci.yml"], {"tooling", "workflows"}, []),
        (["scripts/ci_scope.py", "apps/api/tests/test_ci_scope.py"], {"tooling"}, []),
        (["scripts/release.py"], {"tooling"}, []),
        (["scripts/commerce-workload/generate.py"], {"workload"}, []),
        (["infrastructure/docker/api.Dockerfile"], set(), ["api"]),
        (["Dockerfile"], set(), ["web"]),
        (
            ["infrastructure/connect/ClueDeliveryTransform.java"],
            set(),
            ["connect", "observability-connect"],
        ),
        (
            ["infrastructure/docker/connect.Dockerfile"],
            set(),
            ["connect", "observability-connect"],
        ),
        (
            ["infrastructure/docker/observability-connect.Dockerfile"],
            set(),
            ["observability-connect"],
        ),
        (["infrastructure/observability/jmx-exporter.yaml"], set(), ["observability-connect"]),
        (["infrastructure/observability/compose.yaml"], {"compose"}, []),
        (
            [
                "infrastructure/docker/observability-connect.Dockerfile",
                "infrastructure/observability/compose.yaml",
                "infrastructure/observability/jmx-exporter.yaml",
            ],
            {"compose"},
            ["observability-connect"],
        ),
        ([".dockerignore"], set(), ["api", "connect", "observability-connect", "web"]),
        (["LICENSE", ".gitignore"], set(), []),
        (["apps/api/app/main.py", "apps/web/src/app/page.tsx"], {"api", "web"}, []),
        (["docs/index.md", ".github/workflows/docs.yml"], {"docs", "tooling", "workflows"}, []),
        ([], set(), []),
    ],
)
def test_only_changed_components_are_selected(paths, expected, images):
    result = ci_scope.classify(paths)
    assert {name for name in ci_scope.SCOPES if result[name]} == expected
    assert result["images"] == images


def test_unknown_path_requires_a_rule_instead_of_running_everything():
    with pytest.raises(ValueError, match="new-service/config.toml"):
        ci_scope.classify(["new-service/config.toml"])


@pytest.mark.parametrize("scope", ci_scope.SCOPES)
def test_explicit_manual_or_release_scope_does_not_read_the_diff(monkeypatch, scope):
    monkeypatch.setattr(ci_scope, "git", lambda *args: pytest.fail("No diff for explicit scope"))
    result = ci_scope.select("push", {}, scope)
    assert {name for name in ci_scope.SCOPES if result[name]} == {scope}


def test_unavailable_comparison_fails_instead_of_running_all_jobs(monkeypatch):
    def fail(*args):
        raise subprocess.CalledProcessError(128, ["git"])

    monkeypatch.setattr(ci_scope, "git", fail)
    with pytest.raises(subprocess.CalledProcessError):
        ci_scope.select("push", {"before": "a" * 40})
    with pytest.raises(ValueError, match="valid comparison SHA"):
        ci_scope.select("pull_request", {})


def repository(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "--quiet")
    git("config", "user.name", "CI scope fixture")
    git("config", "user.email", "ci-scope@example.test")
    monkeypatch.setattr(ci_scope, "ROOT", tmp_path)
    return git


def test_rename_checks_old_and_new_folders_and_ignores_unchanged_files(tmp_path, monkeypatch):
    git = repository(tmp_path, monkeypatch)
    web = tmp_path / "apps/web/widget.tsx"
    web.parent.mkdir(parents=True)
    web.write_text("fixture\n")
    (tmp_path / "README.md").write_text("unchanged\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "initial fixture")
    base = git("rev-parse", "HEAD")
    api = tmp_path / "apps/api/widget.py"
    api.parent.mkdir(parents=True)
    web.rename(api)
    git("add", "--all")
    git("commit", "--quiet", "-m", "move fixture")
    for event_name, event in (
        ("push", {"before": base}),
        ("pull_request", {"pull_request": {"base": {"sha": base}}}),
        ("workflow_dispatch", {}),
    ):
        result = ci_scope.select(event_name, event)
        assert result["api"] and result["web"]
        assert not result["docs"]


def test_initial_push_and_root_commit_use_actual_tracked_paths(tmp_path, monkeypatch):
    git = repository(tmp_path, monkeypatch)
    (tmp_path / "README.md").write_text("fixture\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "root fixture")
    for event_name, event in (("push", {"before": "0" * 40}), ("workflow_dispatch", {})):
        result = ci_scope.select(event_name, event)
        assert {name for name in ci_scope.SCOPES if result[name]} == {"docs"}


def test_manual_merge_commit_selects_merged_feature_without_unrelated_base_changes(
    tmp_path, monkeypatch
):
    git = repository(tmp_path, monkeypatch)
    (tmp_path / "README.md").write_text("base\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "base")
    default_branch = git("branch", "--show-current")
    git("checkout", "--quiet", "-b", "feature")
    web = tmp_path / "apps/web/widget.tsx"
    web.parent.mkdir(parents=True)
    web.write_text("feature\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "web feature")
    git("checkout", "--quiet", default_branch)
    (tmp_path / "README.md").write_text("updated on base\n")
    git("commit", "--quiet", "-am", "base docs")
    git("merge", "--quiet", "--no-ff", "-m", "merge feature", "feature")
    result = ci_scope.select("workflow_dispatch", {})
    assert {name for name in ci_scope.SCOPES if result[name]} == {"web"}


@pytest.mark.parametrize(
    "path,component",
    [
        ("Dockerfile", "web"),
        ("infrastructure/docker/observability-connect.Dockerfile", "observability-connect"),
    ],
)
def test_workflow_outputs_encode_only_affected_image_targets(
    tmp_path, monkeypatch, path, component
):
    event = tmp_path / "event.json"
    event.write_text("{}")
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    for key, value in {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(ci_scope, "select", lambda *args: ci_scope.classify([path]))
    ci_scope.main()
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["api"] == values["web"] == "false"
    assert values["images"] == "true"
    assert json.loads(values["image_matrix"]) == {
        "include": [{"component": component, "dockerfile": path}]
    }
    assert "Selected CI scopes" in summary.read_text()
