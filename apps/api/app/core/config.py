import json
import re
from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    environment: Literal["development", "test", "production"] = "development"
    database_url: str
    secret_encryption_key: SecretStr
    connect_secret_token: SecretStr
    auth_mode: Literal["developer", "token", "session"] = "session"
    # JSON mapping SHA256(token) -> {actor, role}; raw tokens are never stored.
    auth_tokens_json: str = "{}"
    bootstrap_default_admin: bool = False
    default_admin_email: str = "admin@cluecdc.local"
    default_admin_password: SecretStr = SecretStr("cluecdc-admin")
    session_secret: SecretStr = SecretStr("development-session-secret-change-me-0001")
    session_ttl_seconds: int = Field(default=28800, ge=300, le=2592000)
    invite_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    public_url: str = "http://localhost:3000"
    reconcile_interval_seconds: int = 10
    worker_concurrency: int = Field(default=4, ge=1, le=32)
    job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    cors_origins: list[str] = []
    integration_timeout_seconds: float = 10
    log_level: Literal["debug", "info", "warn", "warning", "error"] = "info"

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        encryption_key = self.secret_encryption_key.get_secret_value()
        Fernet(encryption_key.encode())
        if len(self.connect_secret_token.get_secret_value()) < 32:
            raise ValueError("Connect secret service token must be at least 32 characters")
        session_secret = self.session_secret.get_secret_value()
        if self.environment != "development" and self.bootstrap_default_admin:
            raise ValueError("The public default Admin is allowed only in development")
        if self.environment == "production" and (
            encryption_key == "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
            or self.connect_secret_token.get_secret_value()
            == "local-connect-token-change-before-production-0001"
        ):
            raise ValueError("Production must not use the example development secrets")
        if self.environment == "production" and self.auth_mode != "session":
            raise ValueError("Production requires session authentication")
        if self.environment == "production" and (
            len(session_secret) < 32
            or session_secret == "development-session-secret-change-me-0001"
        ):
            raise ValueError("Production requires a strong, unique SESSION_SECRET")
        if self.environment == "production" and not self.public_url.startswith("https://"):
            raise ValueError("Production PUBLIC_URL must use HTTPS")
        tokens = json.loads(self.auth_tokens_json)
        if not isinstance(tokens, dict):
            raise ValueError("AUTH_TOKENS_JSON must be a JSON object")
        for token_hash, principal in tokens.items():
            if not isinstance(token_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", token_hash):
                raise ValueError("AUTH_TOKENS_JSON keys must be lowercase SHA-256 hashes")
            if (
                not isinstance(principal, dict)
                or not isinstance(principal.get("actor"), str)
                or not principal["actor"].strip()
                or principal.get("role") not in {"Viewer", "Ops", "Admin"}
            ):
                raise ValueError("AUTH_TOKENS_JSON values require a non-empty actor and valid role")
        if self.auth_mode == "token" and not tokens:
            raise ValueError("Token authentication requires configured token hashes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values are loaded from the environment
