"""Keep local fixture contracts aligned without requiring Docker in unit CI."""

import re
from pathlib import Path

import pytest
import yaml
from scripts.cdc_acceptance_support import load_fixture_environment

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = yaml.safe_load((ROOT / "compose.test.yaml").read_text(encoding="utf-8"))
ENV = dict(
    line.split("=", 1)
    for line in (ROOT / ".env.test.example").read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
)


@pytest.mark.parametrize(
    ("service", "source_password", "destination_password"),
    [
        (
            "postgres-demo",
            "POSTGRES_DEMO_SOURCE_PASSWORD",
            "POSTGRES_DEMO_DESTINATION_PASSWORD",
        ),
        (
            "mysql-demo",
            "MYSQL_DEMO_SOURCE_PASSWORD",
            "MYSQL_DEMO_DESTINATION_PASSWORD",
        ),
    ],
)
def test_shared_database_fixtures_have_clear_credentials_and_authenticated_health(
    service, source_password, destination_password
):
    fixture = FIXTURES["services"][service]
    assert fixture["environment"][source_password] == f"${{{source_password}:-source123}}"
    assert fixture["environment"][destination_password] == (
        f"${{{destination_password}:-destination123}}"
    )
    assert fixture["healthcheck"]["test"][0] == "CMD-SHELL"
    health = fixture["healthcheck"]["test"][1]
    for role, password_variable in (
        ("source", source_password),
        ("destination", destination_password),
    ):
        assert f"cluecdc_{role}" in health and f"{role}_user" in health
        assert f"$${password_variable}" in health
    assert "SELECT 1" in health
    assert all(port.startswith("127.0.0.1:") for port in fixture["ports"])
    config = fixture["configs"][0]
    assert config["target"].startswith("/docker-entrypoint-initdb.d/")
    init = FIXTURES["configs"][config["source"]]["content"]
    for role in ("source", "destination"):
        assert f"cluecdc_{role}" in init and f"{role}_user" in init
    assert all(not volume.startswith("./") for volume in fixture["volumes"])


def test_inline_configs_do_not_interpolate_runtime_shell_passwords():
    for name, config in FIXTURES["configs"].items():
        assert set(config) == {"content"}
        content = config["content"]
        assert not re.search(r"(?<!\$)\$(?!\$)", content)
        if "mysql" in name:
            assert "source_password_hex=$$(" in content
            assert "destination_password_hex=$$(" in content
            assert "CONVERT(X'$${source_password_hex}' USING utf8mb4)" in content
            assert "QUOTE(@source_password)" in content
    assert "Xin chào 👋" in FIXTURES["configs"]["mysql-demo-init"]["content"]


def test_compose_password_defaults_match_example_environment():
    for fixture in FIXTURES["services"].values():
        for value in fixture.get("environment", {}).values():
            if isinstance(value, str) and (match := re.fullmatch(r"\$\{(\w+):-([^}]+)}", value)):
                variable, default = match.groups()
                assert ENV[variable] == default


def test_workload_and_minio_share_fixture_configuration():
    services = FIXTURES["services"]
    workload = services["commerce-generator"]["environment"]
    assert workload["PGHOST"] == "postgres-demo"
    assert workload["MYSQL_HOST"] == "mysql-demo"
    assert workload["PGDATABASE"] == workload["MYSQL_DATABASE"] == "cluecdc_source"
    assert workload["PGUSER"] == workload["MYSQL_USER"] == "source_user"
    bootstrap = services["minio-bootstrap"]
    assert bootstrap["environment"] == services["minio"]["environment"]
    assert bootstrap["configs"][0]["source"] == "minio-init"
    assert (
        services["cluecdc-api"]["depends_on"]["minio-bootstrap"]["condition"]
        == "service_completed_successfully"
    )
    compile(FIXTURES["configs"]["minio-init"]["content"], "minio-init", "exec")


def test_fixture_scripts_use_defaults_and_allow_file_and_shell_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTGRES_DEMO_SOURCE_PASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_DEMO_DESTINATION_PASSWORD", raising=False)
    custom = tmp_path / "fixture.env"
    custom.write_text("POSTGRES_DEMO_SOURCE_PASSWORD=custom-source\n", encoding="utf-8")
    environment = load_fixture_environment([custom])
    assert environment["POSTGRES_DEMO_SOURCE_PASSWORD"] == "custom-source"
    assert environment["POSTGRES_DEMO_DESTINATION_PASSWORD"] == "destination123"
    monkeypatch.setenv("POSTGRES_DEMO_SOURCE_PASSWORD", "shell-source")
    assert load_fixture_environment([custom])["POSTGRES_DEMO_SOURCE_PASSWORD"] == "shell-source"
