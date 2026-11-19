"""Guard credentials and encryption keys across fresh setups and upgrades."""

import base64

from scripts.bootstrap import bootstrap, read_values
from scripts.cdc_acceptance_support import load_fixture_environment


def test_fresh_secrets_are_independent_valid_and_idempotent(tmp_path):
    path = tmp_path / ".env"
    assert bootstrap(path)
    initial = path.read_bytes()
    values = read_values(initial.decode())
    assert len(base64.urlsafe_b64decode(values["SECRET_ENCRYPTION_KEY"])) == 32
    assert (
        len(
            {
                values[key]
                for key in (
                    "CLUECDC_LOCAL_PASSWORD",
                    "SECRET_ENCRYPTION_KEY",
                    "CONNECT_SECRET_TOKEN",
                    "SESSION_SECRET",
                )
            }
        )
        == 4
    )
    assert "METADATA_PASSWORD" not in values
    assert not bootstrap(path)
    assert path.read_bytes() == initial


def test_upgrade_preserves_public_encryption_key_and_custom_credentials(tmp_path):
    path = tmp_path / ".env"
    original = (
        "SECRET_ENCRYPTION_KEY=MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=\n"
        "METADATA_PASSWORD='existing-password'\n"
        "POSTGRES_DEMO_SOURCE_PASSWORD=custom-source\n"
        "SESSION_SECRET=existing-session-value-with-32-characters\n"
        "CONNECT_SECRET_TOKEN=existing-service-value-with-32-characters\n"
    )
    path.write_text(original)
    bootstrap(path)
    values = read_values(path.read_text())
    for key, value in read_values(original).items():
        assert values[key] == value
    assert values["POSTGRES_DEMO_DESTINATION_PASSWORD"] == "destination123"
    assert values["MYSQL_DEMO_ADMIN_PASSWORD"] == "mysql123"
    assert values["MINIO_SECRET_KEY"] == "minioadmin"
    assert not bootstrap(path)


def test_shared_password_and_per_role_shell_override_match_compose(tmp_path, monkeypatch):
    for key in (
        "CLUECDC_LOCAL_PASSWORD",
        "MYSQL_DEMO_SOURCE_PASSWORD",
        "MINIO_SECRET_KEY",
        "POSTGRES_DEMO_SOURCE_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text("CLUECDC_LOCAL_PASSWORD=shared-password\nMYSQL_DEMO_SOURCE_PASSWORD=custom\n")
    values = load_fixture_environment([path])
    assert values["POSTGRES_DEMO_SOURCE_PASSWORD"] == "shared-password"
    assert values["MYSQL_DEMO_SOURCE_PASSWORD"] == "custom"
    assert values["MINIO_SECRET_KEY"] == "shared-password"
    monkeypatch.setenv("MYSQL_DEMO_SOURCE_PASSWORD", "shell-password")
    assert load_fixture_environment([path])["MYSQL_DEMO_SOURCE_PASSWORD"] == "shell-password"
