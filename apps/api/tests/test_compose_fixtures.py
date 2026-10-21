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
    ("service", "engine", "role", "password_variable"),
    [
        ("cdc-source-postgres", "POSTGRES", "source", "SOURCE_PASSWORD"),
        ("cdc-source-mysql", "MYSQL", "source", "MYSQL_CDC_PASSWORD"),
        ("destination-postgres", "POSTGRES", "destination", "DESTINATION_PASSWORD"),
        ("destination-mysql", "MYSQL", "destination", "MYSQL_DELIVERY_PASSWORD"),
    ],
)
def test_database_fixture_names_credentials_and_authenticated_health(
    service, engine, role, password_variable
):
    fixture = FIXTURES["services"][service]
    database = f"cluecdc_{role}"
    user = f"{role}_user"
    assert (
        fixture["environment"]["POSTGRES_DB" if engine == "POSTGRES" else "MYSQL_DATABASE"]
        == database
    )
    assert fixture["healthcheck"]["test"][0] == "CMD-SHELL"
    health = fixture["healthcheck"]["test"][1]
    assert database in health and user in health
    assert f"$${password_variable}" in health
    assert "SELECT 1" in health
    assert all(port.startswith("127.0.0.1:") for port in fixture["ports"])
    config = fixture["configs"][0]
    assert config["target"].startswith("/docker-entrypoint-initdb.d/")
    init = FIXTURES["configs"][config["source"]]["content"]
    assert database in init and user in init
    assert all(not volume.startswith("./") for volume in fixture["volumes"])


def test_inline_configs_do_not_interpolate_runtime_shell_passwords():
    for name, config in FIXTURES["configs"].items():
        assert set(config) == {"content"}
        content = config["content"]
        assert not re.search(r"(?<!\$)\$(?!\$)", content)
        if "mysql" in name:
            assert "password_hex=$$(" in content
            assert "CONVERT(X'$${password_hex}' USING utf8mb4)" in content
            assert "QUOTE(@fixture_password)" in content
    assert "Xin chào 👋" in FIXTURES["configs"]["source-mysql-init"]["content"]


def test_compose_password_defaults_match_example_environment():
    for fixture in FIXTURES["services"].values():
        for value in fixture.get("environment", {}).values():
            if isinstance(value, str) and (match := re.fullmatch(r"\$\{(\w+):-([^}]+)}", value)):
                variable, default = match.groups()
                assert ENV[variable] == default


def test_workload_and_minio_share_fixture_configuration():
    services = FIXTURES["services"]
    workload = services["commerce-generator"]["environment"]
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
    monkeypatch.delenv("SOURCE_PASSWORD", raising=False)
    monkeypatch.delenv("DESTINATION_PASSWORD", raising=False)
    custom = tmp_path / "fixture.env"
    custom.write_text("SOURCE_PASSWORD=custom-source\n", encoding="utf-8")
    environment = load_fixture_environment([custom])
    assert environment["SOURCE_PASSWORD"] == "custom-source"
    assert environment["DESTINATION_PASSWORD"] == "destination123"
    monkeypatch.setenv("SOURCE_PASSWORD", "shell-source")
    assert load_fixture_environment([custom])["SOURCE_PASSWORD"] == "shell-source"
