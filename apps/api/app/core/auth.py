import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.errors import DomainError

PERMISSIONS = {
    "Viewer": {"sources.read", "pipelines.read", "kafka.read", "connect.read"},
    "DataEngineer": {
        "sources.read",
        "sources.write",
        "pipelines.read",
        "pipelines.write",
        "pipelines.operate",
        "kafka.read",
        "connect.read",
        "connect.operate",
    },
    "PlatformAdmin": {
        "sources.read",
        "sources.write",
        "pipelines.read",
        "pipelines.write",
        "pipelines.operate",
        "kafka.read",
        "kafka.topic.delete",
        "connect.read",
        "connect.operate",
        "audit.read",
        "settings.manage",
    },
    "Admin": {"*"},
}
security = HTTPBearer(auto_error=False)

PERMISSIONS["Viewer"].add("destinations.read")
for _role in ["DataEngineer", "PlatformAdmin"]:
    PERMISSIONS[_role].update({"destinations.read", "destinations.write", "destinations.operate"})


@dataclass
class Principal:
    actor: str
    role: str


class AuthProvider(Protocol):
    async def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> Principal: ...


class DeveloperAuthProvider:
    async def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> Principal:
        return Principal("local-developer", "Admin")


class TokenAuthProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> Principal:
        token_hash = (
            hashlib.sha256(credentials.credentials.encode()).hexdigest() if credentials else ""
        )
        user = json.loads(self.settings.auth_tokens_json).get(token_hash)
        if not user or user.get("role") not in PERMISSIONS:
            raise DomainError("UNAUTHENTICATED", "A valid bearer token is required", 401)
        return Principal(user["actor"], user["role"])


def auth_provider(settings: Settings) -> AuthProvider:
    providers: dict[str, AuthProvider] = {
        "developer": DeveloperAuthProvider(),
        "token": TokenAuthProvider(settings),
    }
    return providers[settings.auth_mode]


async def principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> Principal:
    settings = get_settings()
    return await auth_provider(settings).authenticate(credentials)


def require(permission: str):
    async def dependency(user: Principal = Depends(principal)) -> Principal:
        granted = PERMISSIONS[user.role]
        if "*" not in granted and permission not in granted:
            raise DomainError("FORBIDDEN", "You do not have permission for this operation", 403)
        return user

    return dependency
