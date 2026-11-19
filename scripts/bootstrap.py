"""Fill missing local credentials without rotating established values or touching Docker."""

import argparse
import base64
import os
import pathlib
import secrets

ROOT = pathlib.Path(__file__).resolve().parent.parent
TECHNICAL_SECRETS = ("SECRET_ENCRYPTION_KEY", "CONNECT_SECRET_TOKEN", "SESSION_SECRET")


def read_values(contents: str) -> dict[str, str]:
    values = {}
    for line in contents.splitlines():
        key, separator, value = line.partition("=")
        if separator and not key.lstrip().startswith("#"):
            values[key.strip().removeprefix("export ")] = value.strip().strip("\"'")
    return values


def bootstrap(path: pathlib.Path) -> bool:
    existing = path.exists()
    contents = (
        path.read_text(encoding="utf-8")
        if existing
        else (ROOT / ".env.example").read_text(encoding="utf-8")
    )
    values = read_values(contents)
    additions = {}
    if not values.get("CLUECDC_LOCAL_PASSWORD"):
        additions["CLUECDC_LOCAL_PASSWORD"] = secrets.token_hex(16)
        # Old absent per-service values selected public defaults. Pin them for
        # upgrades: environment edits cannot rotate passwords in existing volumes.
        if existing:
            legacy = {
                "POSTGRES_DEMO_ADMIN_PASSWORD": "postgres123",
                "POSTGRES_DEMO_SOURCE_PASSWORD": "source123",
                "POSTGRES_DEMO_DESTINATION_PASSWORD": "destination123",
                "MYSQL_DEMO_ADMIN_PASSWORD": "mysql123",
                "MYSQL_DEMO_SOURCE_PASSWORD": "source123",
                "MYSQL_DEMO_DESTINATION_PASSWORD": "destination123",
                "MINIO_SECRET_KEY": "minioadmin",
                "CLICKHOUSE_TEST_PASSWORD": "clickhouse123",
            }
            additions.update(
                {key: value for key, value in legacy.items() if not values.get(key)}
            )
    for key in TECHNICAL_SECRETS:
        if not values.get(key):
            additions[key] = (
                base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
                if key == "SECRET_ENCRYPTION_KEY"
                else secrets.token_hex(32)
            )
    if not additions and existing:
        print("Environment already complete; left unchanged.")
        return False
    lines = contents.splitlines()
    for key, value in additions.items():
        # Preserve every nonempty value, including historical public keys that
        # may already protect persistent metadata.
        lines = [line for line in lines if line.partition("=")[0].strip() != key]
        lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(lines) + "\n")
    print(
        "Filled missing local credentials; existing values preserved. Keep this file private."
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=pathlib.Path, default=ROOT / ".env")
    args = parser.parse_args()
    bootstrap(args.env_file)


if __name__ == "__main__":
    main()
