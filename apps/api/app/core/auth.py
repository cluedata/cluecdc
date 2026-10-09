import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import session_dependency
from app.core.errors import DomainError
from app.models.entities import AuthSession, User

SESSION_COOKIE = "cluecdc_session"

PERMISSIONS = {
    "Viewer": {
        "overview.read",
        "pipelines.read",
        "deliveries.read",
    },
    "Ops": {
        "overview.read",
        "sources.read",
        "destinations.read",
        "pipelines.read",
        "pipelines.write",
        "pipelines.operate",
        "pipelines.admin",
        "deliveries.read",
        "deliveries.write",
        "deliveries.operate",
        "deliveries.admin",
        "kafka.read",
        "connect.read",
    },
    "Admin": {"*"},
}
security = HTTPBearer(auto_error=False)


def session_token_hash(token: str, settings: Settings | None = None) -> str:
    key = (settings or get_settings()).session_secret.get_secret_value().encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@dataclass
class Principal:
    actor: str
    role: str
    user_id: UUID | None = None


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
            raise DomainError("UNAUTHENTICATED", "Authentication is required", 401)
        return Principal(user["actor"], user["role"])


async def _session_principal(request: Request, db: AsyncSession, settings: Settings) -> Principal:
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    if not raw_token:
        raise DomainError("UNAUTHENTICATED", "Authentication is required", 401)
    row = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == session_token_hash(raw_token, settings))
    )
    result = row.first()
    if result is None:
        raise DomainError("UNAUTHENTICATED", "Authentication is required", 401)
    auth_session, user = result
    if _aware(auth_session.expires_at) <= datetime.now(UTC) or user.status != "ACTIVE":
        if _aware(auth_session.expires_at) <= datetime.now(UTC):
            await db.delete(auth_session)
            await db.commit()
        raise DomainError("UNAUTHENTICATED", "Authentication is required", 401)
    return Principal(user.email, user.role.title(), user.id)


async def principal(
    request: Request,
    db: Annotated[AsyncSession, Depends(session_dependency)],
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> Principal:
    settings = get_settings()
    if settings.auth_mode == "developer":
        developer = await DeveloperAuthProvider().authenticate(credentials)
        admin = await db.scalar(
            select(User)
            .where(User.role == "ADMIN", User.status == "ACTIVE")
            .order_by(User.created_at)
            .limit(1)
        )
        if admin:
            return Principal(admin.email, "Admin", admin.id)
        return developer
    if settings.auth_mode == "token":
        return await TokenAuthProvider(settings).authenticate(credentials)
    return await _session_principal(request, db, settings)


def require(permission: str):
    async def dependency(user: Principal = Depends(principal)) -> Principal:
        granted = PERMISSIONS[user.role]
        if "*" not in granted and permission not in granted:
            raise DomainError("FORBIDDEN", "You do not have permission for this operation", 403)
        return user

    return dependency
