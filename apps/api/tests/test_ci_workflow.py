"""Keep workflow conditions in sync with the path selector, on both native CPUs."""

import ast
import re
from pathlib import Path

import pytest
import yaml
from scripts import ci_changes

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
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


def test_core_bake_does_not_wait_for_go_or_java_builder_cache_exports():
    step = STEPS["Build runtime images once with persistent per-architecture caches"]
    assert "steps.connect-image.outputs.cache-hit" in step["with"]["targets"]
    assert "minio" not in step["with"]["targets"]
    assert STEPS["Reuse the final native Connect image"]["with"]["defer-build"] == "true"
    assert (
        "docker image save"
        in STEPS["Save the native Connect fixture after a cache-miss parallel build"]["run"]
    )
    assert "ci-connect" not in step["with"]["set"]
    assert "ci-minio" not in step["with"]["set"]
    assert "timeout=60s" in step["with"]["set"]


def test_final_fixture_cache_includes_architecture_and_immutable_base_images():
    action = yaml.safe_load((ROOT / ".github/actions/cached-ci-image/action.yml").read_text())
    cache = next(step for step in action["runs"]["steps"] if step.get("id") == "image-cache")
    assert "runner.arch" in cache["with"]["key"]
    assert "steps.bases.outputs.digest" in cache["with"]["key"]
    assert "restore-keys" not in cache["with"]


def test_embedded_python_steps_compile():
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        for job in yaml.safe_load(workflow.read_text())["jobs"].values():
            for step in job.get("steps", []):
                if step.get("shell") == "python":
                    compile(step["run"], str(workflow), "exec")


def test_database_only_profile_keeps_s3_bootstrap_optional_but_waits_for_completion_when_enabled():
    config = yaml.load((ROOT / ".github/compose.test.ci.yaml").read_text(), Loader=yaml.BaseLoader)
    services = config["services"]
    assert services["minio"]["profiles"] == ["object-storage"]
    assert services["minio-bootstrap"]["profiles"] == ["object-storage"]
    dependency = services["cluecdc-api"]["depends_on"]["minio-bootstrap"]
    assert dependency == {"condition": "service_completed_successfully", "required": "false"}
