import json
import runpy
from pathlib import Path

import pytest


def test_initializer_escapes_secrets_and_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = {
        "SOURCE_PASSWORD": 'source"\\$&@:',
        "MYSQL_SOURCE_PASSWORD": 'mysql"\\$&@:',
        "DESTINATION_PASSWORD": 'destination"\\$&@:',
    }
    monkeypatch.setenv("CLOUDBEAVER_WORKSPACE", str(tmp_path))
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    initializer = Path(__file__).with_name("init.py")
    runpy.run_path(str(initializer))

    target = tmp_path / "GlobalConfiguration" / ".dbeaver" / "data-sources.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert (
        data["connections"]["cluecdc-postgres-source"]["configuration"][
            "auth-properties"
        ]["userPassword"]
        == secrets["SOURCE_PASSWORD"]
    )
    assert (
        data["connections"]["cluecdc-mysql-source"]["configuration"]["auth-properties"][
            "userPassword"
        ]
        == secrets["MYSQL_SOURCE_PASSWORD"]
    )
    assert (
        data["connections"]["cluecdc-postgres-destination"]["configuration"][
            "auth-properties"
        ]["userPassword"]
        == secrets["DESTINATION_PASSWORD"]
    )
    assert {
        connection["configuration"]["host"]
        for connection in data["connections"].values()
    } == {"cdc-source-postgres", "cdc-source-mysql", "destination-postgres"}
    assert all(connection["read-only"] for connection in data["connections"].values())
    before = target.read_bytes()

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(initializer))

    assert exit_info.value.code == 0
    assert target.read_bytes() == before
