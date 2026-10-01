from typing import Any


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
            if any(word in k.lower() for word in ("password", "credential", "token", "secret"))
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
