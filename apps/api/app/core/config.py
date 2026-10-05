import json
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
    auth_mode: Literal["developer", "token"] = "developer"
    # JSON mapping SHA256(token) -> {actor, role}; raw tokens are never stored.
    auth_tokens_json: str = "{}"
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
        if self.environment == "production" and self.auth_mode != "token":
            raise ValueError("Production requires token authentication")
        if self.environment == "production" and (
            encryption_key == "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
            or self.connect_secret_token.get_secret_value()
            == "local-connect-token-change-before-production-0001"
        ):
            raise ValueError("Production must not use the example development secrets")
        tokens = json.loads(self.auth_tokens_json)
        if self.auth_mode == "token" and not tokens:
            raise ValueError("Token authentication requires configured token hashes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values are loaded from the environment
