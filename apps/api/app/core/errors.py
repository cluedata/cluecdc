from typing import Any

SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "credential",
    "token",
    "secret",
    "access_key",
    "access.key",
    "private_key",
    "private.key",
    "sasl.jaas.config",
    "ssl.key.pem",
    "ssl.keystore",
    "ssl_ca_pem",
    "ssl_certificate_pem",
    "ssl_key_pem",
    "sasl.username",
    "sasl.password",
)


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def redact(value: Any, secrets: list[str] | None = None) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if any(word in k.lower() for word in SENSITIVE_KEY_MARKERS)
            else redact(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets or []:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value
