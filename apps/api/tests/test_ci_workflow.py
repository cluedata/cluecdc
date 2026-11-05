"""Automatic API checks and optional extended validation must stay separate."""

import ast
import re
from pathlib import Path

import pytest
import yaml
from scripts import ci_changes

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/extended-validation.yml").read_text())
STEPS = {step.get("name"): step for step in WORKFLOW["jobs"]["containers-and-smoke"]["steps"]}
ACCEPTANCE = {
    "minio": "Reuse the final S3 fixture image only when needed",
    "fixtures": "Add integration fixtures to the running core stack",
    "host": "Install only the host dependencies used by acceptance scripts",
    "browser": "Verify browser flows",
    "seed": "Seed and verify live capture for CDC or integration browser tests",
    "jdbc": "Verify JDBC destination delivery",
    "s3": "Verify real S3 object contents",
    "secrets": "Verify secret boundaries",
    "workload": "Build the optional workload tool only when it changes",
    "arm-dependencies": "Verify ARM64 workload native dependencies without database fixtures",
}


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_automatic_ci_only_runs_selected_jobs_and_preserves_required_check():
    ci = workflow("ci.yml")
    assert set(ci["on"]) == {"pull_request", "push", "workflow_dispatch", "workflow_call"}
    scoped = {
        "api",
        "web",
        "docs",
        "compose",
        "deployment",
        "tooling",
        "workflows",
        "workload",
        "images",
    }
    assert set(ci["jobs"]) == scoped | {"changes", "checks"}
    for name in scoped:
        assert ci["jobs"][name]["needs"] == "changes"
        assert ci["jobs"][name]["if"] == f"needs.changes.outputs.{name} == 'true'"
    check = ci["jobs"]["checks"]
    assert check["name"] == "CI checks"
    assert check["if"] == "always()"
    assert set(check["needs"]) == scoped | {"changes"}
    assert "success" in check["steps"][0]["run"] and "skipped" in check["steps"][0]["run"]
    assert ci["jobs"]["changes"]["steps"][0]["with"]["fetch-depth"] == "0"
    job = ci["jobs"]["api"]
    assert job["env"]["WORKER_TEST_DATABASE_URL"].startswith("postgresql+asyncpg://")
    assert job["services"]["worker-test-postgres"]["image"] == "postgres:17.9"
    assert job["env"]["DQ_LIVE_TESTS"] == "0"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "pytest apps/api/tests" in commands
    assert "--junitxml=" in commands
    assert not any(tool in commands for tool in ("npm", "playwright", "docker", "kubectl"))
    web_commands = "\n".join(step.get("run", "") for step in ci["jobs"]["web"]["steps"])
    assert "pytest" not in web_commands and "playwright" not in web_commands
    assert "npm run test:web" in web_commands


@pytest.mark.parametrize(
    "name",
    [
        "extended-validation.yml",
        "hardening.yml",
        "data-quality.yml",
        "cdc-reliability.yml",
        "source-cdc.yml",
        "docs.yml",
    ],
)
def test_extended_checks_and_documentation_only_run_when_requested(name):
    configuration = workflow(name)
    assert "workflow_dispatch" in configuration["on"]
    assert set(configuration["on"]) <= {"workflow_dispatch", "workflow_call"}


def condition(expression, gates, arch):
    expression = (
        re.sub(
            r"needs\.changes\.outputs\.(\w+)",
            lambda match: repr(str(gates[match[1]]).lower()),
            expression,
        )
        .replace("matrix.arch", repr(arch))
        .replace("&&", " and ")
        .replace("||", " or ")
    )

    def evaluate(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [evaluate(child) for child in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return evaluate(node.left) == evaluate(node.comparators[0])
        raise AssertionError(f"Unsupported condition: {ast.dump(node)}")

    return evaluate(ast.parse(expression, mode="eval").body)


@pytest.mark.parametrize(
    ("paths", "arch", "expected"),
    [
        ([".github/workflows/ci.yml"], "amd64", set(ACCEPTANCE) - {"arm-dependencies"}),
        ([".github/workflows/ci.yml"], "arm64", {"workload", "arm-dependencies"}),
        (["apps/web/src/components/shell.tsx"], "amd64", {"browser"}),
        (["apps/web/src/components/shell.tsx"], "arm64", set()),
        (["scripts/commerce-workload/generate.py"], "amd64", {"fixtures", "workload"}),
        (["scripts/commerce-workload/generate.py"], "arm64", {"workload", "arm-dependencies"}),
        (
            ["scripts/commerce-workload/generate.py", "apps/web/src/components/shell.tsx"],
            "amd64",
            {"fixtures", "browser", "workload"},
        ),
        (
            ["apps/api/app/services/object_storage.py"],
            "amd64",
            {"minio", "fixtures", "host", "s3", "secrets"},
        ),
        (
            ["apps/web/src/features/pipelines/wizard.tsx"],
            "amd64",
            {"minio", "fixtures", "host", "seed", "browser", "secrets"},
        ),
    ],
)
def test_only_required_acceptance_steps_run(paths, arch, expected):
    gates = ci_changes.classify(paths)
    assert {
        key for key, name in ACCEPTANCE.items() if condition(STEPS[name]["if"], gates, arch)
    } == expected


def test_final_fixture_cache_includes_architecture_and_immutable_base_images():
    action = yaml.safe_load((ROOT / ".github/actions/cached-ci-image/action.yml").read_text())
    cache = next(step for step in action["runs"]["steps"] if step.get("id") == "image-cache")
    assert "runner.arch" in cache["with"]["key"]
    assert "steps.bases.outputs.digest" in cache["with"]["key"]
    assert "restore-keys" not in cache["with"]


def test_database_only_profile_keeps_s3_bootstrap_optional_but_waits_for_completion_when_enabled():
    config = yaml.load((ROOT / ".github/compose.test.ci.yaml").read_text(), Loader=yaml.BaseLoader)
    services = config["services"]
    assert services["minio"]["profiles"] == ["object-storage"]
    assert services["minio-bootstrap"]["profiles"] == ["object-storage"]
    dependency = services["cluecdc-api"]["depends_on"]["minio-bootstrap"]
    assert dependency == {"condition": "service_completed_successfully", "required": "false"}


def test_browser_integration_uses_session_auth_before_recreating_services():
    run = STEPS["Add integration fixtures to the running core stack"]["run"]
    export = "export AUTH_MODE=session"
    persisted = 'echo "AUTH_MODE=session" >> "$GITHUB_ENV"'
    assert export in run
    assert persisted in run
    assert run.index(export) < run.index("docker compose up")
