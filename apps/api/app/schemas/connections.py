from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    category: Literal["DATABASE"] = "DATABASE"
    provider: Literal["POSTGRESQL", "MYSQL"]
    config: dict
    credentials: dict[str, SecretStr] = Field(default_factory=dict)
    capabilities: list[Literal["SOURCE", "DESTINATION"]] | None = None

    @model_validator(mode="after")
    def validate_connection(self) -> "ConnectionInput":
        allowed = {
            "host",
            "port",
            "database_name",
            "username",
            "ssl_enabled",
            "environment",
            "provider_options",
        }
        unknown = set(self.config) - allowed
        if unknown:
            raise ValueError("Unsupported config fields: " + ", ".join(sorted(unknown)))
        for key in ("host", "database_name", "username"):
            if not str(self.config.get(key, "")).strip():
                raise ValueError(f"{key} is required")
        defaults = {"POSTGRESQL": 5432, "MYSQL": 3306}
        port = self.config.get("port", defaults[self.provider])
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.config.setdefault("port", port)
        self.config.setdefault("ssl_enabled", False)
        self.config.setdefault("environment", "DEV")
        self.config.setdefault("provider_options", {})
        if self.capabilities is None:
            self.capabilities = ["SOURCE", "DESTINATION"]
        if not self.capabilities:
            raise ValueError("A database connection needs at least one capability")
        unknown_secrets = set(self.credentials) - {"password"}
        if unknown_secrets:
            raise ValueError("Unsupported credential fields: " + ", ".join(sorted(unknown_secrets)))
        return self
