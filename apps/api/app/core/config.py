import json
import re
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

KNOWN_DEVELOPMENT_SECRETS = {
    "cluecdc-admin",
    "cluecdc-metadata-local-only",
    "development-session-secret-change-me-0001",
    "local-connect-token-change-before-production-0001",
    "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
}


def _origin(value: str, setting: str, *, require_https: bool) -> str:
    """Validate and normalize an HTTP origin without echoing its value."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (require_https and parsed.scheme != "https")
    ):
        scheme = "HTTPS" if require_https else "HTTP(S)"
        raise ValueError(f"{setting} must be a valid {scheme} origin without credentials or a path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{setting} contains an invalid port") from exc
    default_port = (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    authority = parsed.hostname.lower()
    if port is not None and not default_port:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}"


def _strong_secret(value: str, setting: str) -> None:
    if len(value) < 32 or len(set(value)) < 8 or value in KNOWN_DEVELOPMENT_SECRETS:
        raise ValueError(f"Production requires a strong, unique {setting}")


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
    infrastructure_health_interval_seconds: int = Field(default=30, ge=5, le=3600)
    worker_concurrency: int = Field(default=4, ge=1, le=32)
    job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_base_seconds: float = Field(default=2, ge=0.1, le=300)
    job_retry_max_seconds: float = Field(default=60, ge=1, le=3600)
    job_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    connector_operation_lease_seconds: int = Field(default=300, ge=30, le=3600)
    auto_recovery_enabled: bool = True
    auto_recovery_max_attempts: int = Field(default=5, ge=1, le=20)
    auto_recovery_base_delay_seconds: float = Field(default=10, ge=0.1, le=300)
    auto_recovery_max_delay_seconds: float = Field(default=300, ge=1, le=3600)
    auto_recovery_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    cors_origins: list[str] = []
    integration_timeout_seconds: float = 10
    log_level: Literal["debug", "info", "warn", "warning", "error"] = "info"
    allow_insecure_kafka: bool = False
    azure_managed_identity_enabled: bool = False

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if self.job_retry_max_seconds < self.job_retry_base_seconds:
            raise ValueError("JOB_RETRY_MAX_SECONDS must be at least JOB_RETRY_BASE_SECONDS")
        if self.auto_recovery_max_delay_seconds < self.auto_recovery_base_delay_seconds:
            raise ValueError(
                "AUTO_RECOVERY_MAX_DELAY_SECONDS must be at least AUTO_RECOVERY_BASE_DELAY_SECONDS"
            )
        encryption_key = self.secret_encryption_key.get_secret_value()
        try:
            Fernet(encryption_key.encode())
        except (TypeError, ValueError) as exc:
            raise ValueError("SECRET_ENCRYPTION_KEY must be a valid Fernet key") from exc
        connect_token = self.connect_secret_token.get_secret_value()
        if len(connect_token) < 32:
            raise ValueError("Connect secret service token must be at least 32 characters")
        session_secret = self.session_secret.get_secret_value()
        if self.environment != "development" and self.bootstrap_default_admin:
            raise ValueError("The public default Admin is allowed only in development")
        if self.environment == "production":
            if self.auth_mode != "session":
                raise ValueError("Production requires session authentication")
            _strong_secret(session_secret, "SESSION_SECRET")
            _strong_secret(connect_token, "CONNECT_SECRET_TOKEN")
            if encryption_key in KNOWN_DEVELOPMENT_SECRETS:
                raise ValueError("Production must not use the example SECRET_ENCRYPTION_KEY")
            if len({session_secret, connect_token, encryption_key}) != 3:
                raise ValueError("Production security secrets must be unique")

            public_origin = _origin(self.public_url, "PUBLIC_URL", require_https=True)
            if not self.cors_origins:
                raise ValueError("Production requires at least one explicit CORS origin")
            normalized_origins: list[str] = []
            for value in self.cors_origins:
                if value == "*":
                    raise ValueError("Production CORS origins must not contain a wildcard")
                normalized_origins.append(_origin(value, "CORS_ORIGINS", require_https=True))
            if len(normalized_origins) != len(set(normalized_origins)):
                raise ValueError("Production CORS origins must be unique")
            if public_origin not in normalized_origins:
                raise ValueError("Production CORS origins must include PUBLIC_URL")
            self.public_url = public_origin
            self.cors_origins = normalized_origins

            try:
                database = make_url(self.database_url)
            except Exception as exc:
                raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc
            if (
                database.get_backend_name() != "postgresql"
                or not database.host
                or not database.database
                or not database.username
                or not database.password
            ):
                raise ValueError(
                    "Production DATABASE_URL must use PostgreSQL with host, "
                    "database, and credentials"
                )
            if str(database.password) in KNOWN_DEVELOPMENT_SECRETS:
                raise ValueError("Production DATABASE_URL must not use an example password")
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
