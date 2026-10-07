from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from app.api.auth import token_hash
from app.cli import create_admin
from app.core.config import get_settings
from app.models.entities import Invite, User

PASSWORD = "a long test passphrase"


@pytest.fixture(autouse=True)
def session_auth(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_mode", "session")


async def add_user(db_factory, email: str, role: str, status: str = "ACTIVE") -> User:
    async with db_factory() as db:
        user = User(
            email=email,
            password_hash=PasswordHasher().hash(PASSWORD),
            role=role,
            status=status,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def sign_in(client, email: str, password: str = PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_create_first_admin_normalizes_email_and_hashes_password(db_factory):
    async with db_factory() as db:
        user = await create_admin(db, "  ADMIN@Example.COM ", PASSWORD)
        assert user.email == "admin@example.com"
        assert user.role == "ADMIN" and user.status == "ACTIVE"
        assert user.password_hash != PASSWORD
        assert PasswordHasher().verify(user.password_hash, PASSWORD)


async def test_login_session_logout_and_generic_failures(client, db_factory):
    await add_user(db_factory, "admin@example.com", "ADMIN")
    unknown = await sign_in(client, "missing@example.com", "incorrect password")
    wrong = await sign_in(client, "admin@example.com", "incorrect password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == "Invalid email or password."
    assert wrong.json()["error"]["message"] == "Invalid email or password."

    logged_in = await sign_in(client, "ADMIN@example.com")
    assert logged_in.status_code == 200
    assert "HttpOnly" in logged_in.headers["set-cookie"]
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["role"] == "Admin"

    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_disabled_user_cannot_login(client, db_factory):
    await add_user(db_factory, "disabled@example.com", "OPS", "DISABLED")
    response = await sign_in(client, "disabled@example.com")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid email or password."


async def test_admin_invites_and_invite_is_single_use(client, db_factory):
    await add_user(db_factory, "admin@example.com", "ADMIN")
    assert (await sign_in(client, "admin@example.com")).status_code == 200
    created = await client.post(
        "/api/v1/users/invite", json={"email": " Ops@Example.com ", "role": "OPS"}
    )
    assert created.status_code == 201
    raw_token = created.json()["invite_url"].rsplit("/", 1)[-1]

    validation = await client.get(f"/api/v1/invites/{raw_token}")
    assert validation.json() == {"email": "ops@example.com", "role": "OPS", "valid": True}
    accepted = await client.post(
        f"/api/v1/invites/{raw_token}/accept",
        json={"password": PASSWORD, "confirm_password": PASSWORD},
    )
    assert accepted.status_code == 200
    assert accepted.json()["user"]["status"] == "ACTIVE"
    assert (await client.get(f"/api/v1/invites/{raw_token}")).status_code == 404
    assert (
        await client.post(
            f"/api/v1/invites/{raw_token}/accept",
            json={"password": PASSWORD, "confirm_password": PASSWORD},
        )
    ).status_code == 404
    audit = (await client.get("/api/v1/audit")).text
    assert raw_token not in audit
    assert PASSWORD not in audit
    assert "USER_INVITED" in audit and "USER_ACTIVATED" in audit


async def test_expired_invite_is_rejected(client, db_factory):
    admin = await add_user(db_factory, "admin@example.com", "ADMIN")
    raw_token = "expired-test-token"
    async with db_factory() as db:
        db.add(
            Invite(
                email="ops@example.com",
                role="OPS",
                token_hash=token_hash(raw_token),
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
                created_by=admin.id,
            )
        )
        await db.commit()
    assert (await client.get(f"/api/v1/invites/{raw_token}")).status_code == 404


@pytest.mark.parametrize("role", ["OPS", "VIEWER"])
async def test_non_admin_cannot_manage_users(client, db_factory, role):
    email = f"{role.lower()}@example.com"
    await add_user(db_factory, email, role)
    assert (await sign_in(client, email)).status_code == 200
    response = await client.post(
        "/api/v1/users/invite", json={"email": "new@example.com", "role": "VIEWER"}
    )
    assert response.status_code == 403


async def test_role_changes_and_role_boundaries(client, db_factory):
    admin = await add_user(db_factory, "admin@example.com", "ADMIN")
    target = await add_user(db_factory, "viewer@example.com", "VIEWER")
    assert (await sign_in(client, admin.email)).status_code == 200
    changed = await client.patch(f"/api/v1/users/{target.id}/role", json={"role": "OPS"})
    assert changed.status_code == 200 and changed.json()["role"] == "OPS"
    await client.post("/api/v1/auth/logout")

    assert (await sign_in(client, target.email)).status_code == 200
    assert (await client.post(f"/api/v1/pipelines/{uuid4()}/pause")).status_code != 403
    assert (
        await client.post(
            "/api/v1/users/invite", json={"email": "blocked@example.com", "role": "VIEWER"}
        )
    ).status_code == 403

    async with db_factory() as db:
        target = await db.scalar(select(User).where(User.email == "viewer@example.com"))
        target.role = "VIEWER"
        await db.commit()
    assert (await client.post(f"/api/v1/pipelines/{uuid4()}/pause")).status_code == 403
