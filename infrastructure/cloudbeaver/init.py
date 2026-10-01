"""Create ClueCDC's initial CloudBeaver connections without replacing user state."""

from __future__ import annotations

import json
import os
from pathlib import Path


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Required environment variable {name} is not set")
    return value


workspace = Path(os.environ.get("CLOUDBEAVER_WORKSPACE", "/workspace"))
configuration_dir = workspace / "GlobalConfiguration" / ".dbeaver"
target = configuration_dir / "data-sources.json"

if target.exists():
    print("CloudBeaver data sources already exist; preserving workspace configuration")
    raise SystemExit(0)


def postgres_connection(
    *,
    connection_id: str,
    name: str,
    description: str,
    folder: str,
    host: str,
    database: str,
    username: str,
    password: str,
) -> tuple[str, dict]:
    return connection_id, {
        "provider": "postgresql",
        "driver": "postgres-jdbc",
        "name": name,
        "description": description,
        "save-password": True,
        "read-only": True,
        "folder": folder,
        "configuration": {
            "host": host,
            "port": "5432",
            "database": database,
            "url": f"jdbc:postgresql://{host}:5432/{database}",
            "configurationType": "MANUAL",
            "auth-model": "native",
            "auth-properties": {"userName": username, "userPassword": password},
        },
    }


connections = dict(
    [
        postgres_connection(
            connection_id="cluecdc-postgres-source",
            name="PostgreSQL Source",
            description="ClueCDC PostgreSQL CDC source",
            folder="ClueCDC/Sources",
            host="cdc-source-postgres",
            database="commerce",
            username="cdc_user",
            password=required("SOURCE_PASSWORD"),
        ),
        (
            "cluecdc-mysql-source",
            {
                "provider": "mysql",
                "driver": "mysql8",
                "name": "MySQL Source",
                "description": "ClueCDC MySQL CDC source",
                "save-password": True,
                "read-only": True,
                "folder": "ClueCDC/Sources",
                "configuration": {
                    "host": "cdc-source-mysql",
                    "port": "3306",
                    "database": "shop",
                    "url": (
                        "jdbc:mysql://cdc-source-mysql:3306/shop"
                        "?allowPublicKeyRetrieval=true&useSSL=false"
                    ),
                    "configurationType": "MANUAL",
                    "auth-model": "native",
                    "auth-properties": {
                        "userName": "cdc_mysql",
                        "userPassword": required("MYSQL_SOURCE_PASSWORD"),
                    },
                },
            },
        ),
        postgres_connection(
            connection_id="cluecdc-postgres-destination",
            name="PostgreSQL Destination",
            description="ClueCDC PostgreSQL CDC destination",
            folder="ClueCDC/Destinations",
            host="destination-postgres",
            database="analytics",
            username="delivery_user",
            password=required("DESTINATION_PASSWORD"),
        ),
    ]
)

configuration_dir.mkdir(parents=True, exist_ok=True)
temporary = target.with_suffix(".json.tmp")
temporary.write_text(
    json.dumps({"folders": {}, "connections": connections}, indent=2) + "\n",
    encoding="utf-8",
)
temporary.chmod(0o600)
temporary.replace(target)
print("Created initial CloudBeaver data sources")
