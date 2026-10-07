import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    PERMISSIONS,
    SESSION_COOKIE,
    Principal,
    principal,
    require,
    session_token_hash,
)
from app.core.config import get_settings
from app.core.database import session_dependency
from app.core.errors import DomainError
from app.models.entities import AuthSession, Invite, User
from app.repositories.metadata import audit_event

router = APIRouter(prefix="/api/v1", tags=["Authentication"])
DB = Annotated[AsyncSession, Depends(session_dependency)]
Admin = Annotated[Principal, Depends(require("users.manage"))]
PASSWORD_MIN_LENGTH = 12
_hasher = PasswordHasher()
_dummy_hash = _hasher.hash("not-a-real-password-value")
_attempts: dict[str, deque[float]] = defaultdict(deque)


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) > 320 or normalized.count("@") != 1:
        raise ValueError("A valid email address is required")
    local, domain = normalized.split("@")
    if not local or not domain or "." not in domain or any(c.isspace() for c in normalized):
        raise ValueError("A valid email address is required")
    return normalized


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def public_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


class LoginInput(BaseModel):
    email: str
    password: SecretStr = Field(max_length=1024)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_email(value)


class InviteInput(BaseModel):
    email: str
    role: Literal["ADMIN", "OPS", "VIEWER"]

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_email(value)


class AcceptInviteInput(BaseModel):
    password: SecretStr = Field(min_length=PASSWORD_MIN_LENGTH, max_length=1024)
    confirm_password: SecretStr = Field(min_length=PASSWORD_MIN_LENGTH, max_length=1024)


class RoleInput(BaseModel):
    role: Literal["ADMIN", "OPS", "VIEWER"]


def _rate_key(email: str) -> str:
    # Email remains stable when the API sits behind the same-origin web proxy,
    # where every browser request otherwise has the proxy's network address.
    return email


def _rate_limited(key: str) -> bool:
    current = time.monotonic()
    attempts = _attempts[key]
    while attempts and attempts[0] < current - 300:
        attempts.popleft()
    return len(attempts) >= 5


def _record_failure(key: str) -> None:
    if key not in _attempts and len(_attempts) >= 10_000:
        _attempts.pop(next(iter(_attempts)))
    _attempts[key].append(time.monotonic())


@router.post("/auth/login")
async def login(data: LoginInput, response: Response, db: DB):
    settings = get_settings()
    key = _rate_key(data.email)
    if _rate_limited(key):
        audit_event(db, data.email, "LOGIN_FAILED", "users", data.email, {"rate_limited": True})
        await db.commit()
        raise DomainError("RATE_LIMITED", "Too many sign-in attempts. Try again later.", 429)
    user = await db.scalar(select(User).where(User.email == data.email))
    password_hash = user.password_hash if user and user.password_hash else _dummy_hash
    valid: bool
    try:
        valid = _hasher.verify(password_hash, data.password.get_secret_value())
    except (VerifyMismatchError, VerificationError):
        valid = False
    if not user or not valid or user.status != "ACTIVE":
        _record_failure(key)
        audit_event(
            db,
            data.email,
            "LOGIN_FAILED",
            "users",
            str(user.id) if user else data.email,
        )
        await db.commit()
        raise DomainError("INVALID_CREDENTIALS", "Invalid email or password.", 401)
    if user.password_hash and _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(data.password.get_secret_value())
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
    db.add(
        AuthSession(
            token_hash=session_token_hash(raw_token, settings),
            user_id=user.id,
            expires_at=expires_at,
        )
    )
    user.last_login_at = datetime.now(UTC)
    audit_event(db, user.email, "LOGIN_SUCCESS", "users", str(user.id))
    await db.commit()
    _attempts.pop(key, None)
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )
    return {"user": public_user(user)}


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, db: DB):
    settings = get_settings()
    raw_token = request.cookies.get(SESSION_COOKIE)
    if raw_token:
        auth_session = await db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == session_token_hash(raw_token, settings)
            )
        )
        if auth_session:
            user = await db.get(User, auth_session.user_id)
            audit_event(
                db,
                user.email if user else "unknown",
                "LOGOUT",
                "users",
                str(user.id) if user else "unknown",
            )
            await db.delete(auth_session)
            await db.commit()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.environment == "production",
        httponly=True,
        samesite="lax",
    )


def session_payload(user: Principal) -> dict:
    return {
        "actor": user.actor,
        "email": user.actor,
        "role": user.role,
        "auth_mode": get_settings().auth_mode,
        "environment": get_settings().environment,
        "permissions": sorted(PERMISSIONS[user.role]),
    }


@router.get("/auth/session")
async def current_session(user: Annotated[Principal, Depends(principal)]):
    return session_payload(user)


@router.get("/users")
async def users(db: DB, user: Admin):
    rows = (await db.scalars(select(User).order_by(User.created_at.desc()))).all()
    return [public_user(item) for item in rows]


@router.post("/users/invite", status_code=201)
async def invite_user(data: InviteInput, db: DB, user: Admin):
    if user.user_id is None:
        raise DomainError("SESSION_REQUIRED", "Sign in as an Admin to invite users", 403)
    existing = await db.scalar(select(User).where(User.email == data.email))
    if existing and existing.status in {"ACTIVE", "DISABLED"}:
        raise DomainError("USER_EXISTS", "A user with this email already exists", 409)
    if existing is None:
        existing = User(email=data.email, password_hash=None, role=data.role, status="INVITED")
        db.add(existing)
        await db.flush()
    else:
        existing.role = data.role
    current = datetime.now(UTC)
    await db.execute(
        update(Invite)
        .where(Invite.email == data.email, Invite.accepted_at.is_(None))
        .values(accepted_at=current)
    )
    raw_token = secrets.token_urlsafe(32)
    invite = Invite(
        email=data.email,
        role=data.role,
        token_hash=token_hash(raw_token),
        expires_at=current + timedelta(seconds=get_settings().invite_ttl_seconds),
        created_by=user.user_id,
    )
    db.add(invite)
    await db.flush()
    audit_event(
        db,
        user.actor,
        "USER_INVITED",
        "users",
        str(existing.id),
        {"email": data.email, "role": data.role},
    )
    await db.commit()
    return {
        "invite_url": f"{get_settings().public_url.rstrip('/')}/invite/{raw_token}",
        "expires_at": invite.expires_at,
    }


async def _valid_invite(db: AsyncSession, token: str) -> Invite:
    if not token or len(token) > 200:
        raise DomainError("INVALID_INVITE", "This invite is invalid or has expired.", 404)
    invite = await db.scalar(select(Invite).where(Invite.token_hash == token_hash(token)))
    if (
        not invite
        or invite.accepted_at is not None
        or aware(invite.expires_at) <= datetime.now(UTC)
    ):
        raise DomainError("INVALID_INVITE", "This invite is invalid or has expired.", 404)
    return invite


@router.get("/invites/{token}")
async def validate_invite(token: str, db: DB):
    invite = await _valid_invite(db, token)
    return {"email": invite.email, "role": invite.role, "valid": True}


@router.post("/invites/{token}/accept")
async def accept_invite(token: str, data: AcceptInviteInput, db: DB):
    password = data.password.get_secret_value()
    if password != data.confirm_password.get_secret_value():
        raise DomainError("PASSWORD_MISMATCH", "Passwords do not match", 422)
    invite = await _valid_invite(db, token)
    claimed = await db.execute(
        update(Invite)
        .where(Invite.id == invite.id, Invite.accepted_at.is_(None))
        .values(accepted_at=datetime.now(UTC))
    )
    if getattr(claimed, "rowcount", 0) != 1:
        raise DomainError("INVALID_INVITE", "This invite is invalid or has expired.", 404)
    user = await db.scalar(select(User).where(User.email == invite.email))
    if user is None:
        user = User(email=invite.email, role=invite.role, status="ACTIVE")
        db.add(user)
        await db.flush()
    elif user.status != "INVITED":
        raise DomainError("INVALID_INVITE", "This invite is invalid or has expired.", 404)
    user.password_hash = _hasher.hash(password)
    user.role = invite.role
    user.status = "ACTIVE"
    audit_event(db, user.email, "USER_ACTIVATED", "users", str(user.id))
    await db.commit()
    return {"user": public_user(user)}


@router.patch("/users/{user_id}/role")
async def change_role(user_id: UUID, data: RoleInput, db: DB, actor: Admin):
    target = await db.get(User, user_id)
    if target is None:
        raise DomainError("NOT_FOUND", "User was not found", 404)
    before = target.role
    target.role = data.role
    audit_event(
        db,
        actor.actor,
        "USER_ROLE_CHANGED",
        "users",
        str(target.id),
        {"before": before, "after": data.role},
    )
    await db.commit()
    return public_user(target)


async def _set_status(user_id: UUID, status: str, db: AsyncSession, actor: Principal) -> dict:
    target = await db.get(User, user_id)
    if target is None:
        raise DomainError("NOT_FOUND", "User was not found", 404)
    if status == "DISABLED" and actor.user_id == target.id:
        raise DomainError("CANNOT_DISABLE_SELF", "You cannot disable your own account", 409)
    target.status = status
    if status == "DISABLED":
        await db.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    action = "USER_DISABLED" if status == "DISABLED" else "USER_ENABLED"
    audit_event(db, actor.actor, action, "users", str(target.id))
    await db.commit()
    return public_user(target)


@router.post("/users/{user_id}/disable")
async def disable_user(user_id: UUID, db: DB, actor: Admin):
    return await _set_status(user_id, "DISABLED", db, actor)


@router.post("/users/{user_id}/enable")
async def enable_user(user_id: UUID, db: DB, actor: Admin):
    target = await db.get(User, user_id)
    if target is None:
        raise DomainError("NOT_FOUND", "User was not found", 404)
    status = "ACTIVE" if target.password_hash else "INVITED"
    return await _set_status(user_id, status, db, actor)
