"""Generate local secrets once; does not overwrite an existing .env."""

import base64
import pathlib
import secrets

root = pathlib.Path(__file__).resolve().parent.parent
path = root / ".env"
generated_keys = [
    "SECRET_ENCRYPTION_KEY",
    "CONNECT_SECRET_TOKEN",
    "METADATA_PASSWORD",
]

example_values = {
    "SECRET_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    "CONNECT_SECRET_TOKEN": "local-connect-token-change-before-production-0001",
    "METADATA_PASSWORD": "cluecdc-metadata-local-only",
}


def generate_password(key: str) -> str:
    if key == "SECRET_ENCRYPTION_KEY":
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    if key == "CONNECT_SECRET_TOKEN":
        return secrets.token_hex(32)
    return secrets.token_hex(16)


if path.exists():
    changed = False
    contents = path.read_text()
    lines = contents.splitlines()
    present = set()
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        present.add(key)
        if key in generated_keys and (not value or value == example_values.get(key)):
            lines[index] = key + "=" + generate_password(key)
            changed = True
    missing = [key for key in generated_keys if key not in present]
    if missing:
        lines.extend(key + "=" + generate_password(key) for key in missing)
        changed = True
    if changed:
        path.write_text("\n".join(lines) + "\n")
        print("Generated missing/example local secrets; custom values were preserved.")
    else:
        print(".env already exists; left unchanged")
else:
    values = {
        "ENVIRONMENT": "development",
        "AUTH_MODE": "developer",
        "AUTH_TOKENS_JSON": "{}",
        "SECRET_ENCRYPTION_KEY": base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode(),
        "CONNECT_SECRET_TOKEN": secrets.token_hex(32),
        "METADATA_PASSWORD": secrets.token_hex(16),
    }
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    print("Created .env with unique local secrets. Keep it private and back it up.")
