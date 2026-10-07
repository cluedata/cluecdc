import argparse
import asyncio
import getpass
from collections.abc import Sequence

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import PASSWORD_MIN_LENGTH, normalize_email
from app.core.database import Session, engine
from app.models.entities import User
from app.repositories.metadata import audit_event


async def create_admin(db: AsyncSession, email: str, password: str) -> User:
    normalized = normalize_email(email)
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if await db.scalar(select(User).where(User.email == normalized)):
        raise ValueError("A user with this email already exists")
    user = User(
        email=normalized,
        password_hash=PasswordHasher().hash(password),
        role="ADMIN",
        status="ACTIVE",
    )
    db.add(user)
    await db.flush()
    audit_event(db, normalized, "USER_ACTIVATED", "users", str(user.id), {"bootstrap": True})
    await db.commit()
    return user


async def _run_create_admin(email: str | None) -> int:
    selected_email = email or input("Email: ").strip()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    try:
        async with Session() as db:
            user = await create_admin(db, selected_email, password)
        print(f"Admin created: {user.email}")
        return 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="Create an Admin account")
    create.add_argument("--email")
    args = parser.parse_args(argv)
    try:
        if args.command == "create-admin":
            return asyncio.run(_run_create_admin(args.email))
    except (EOFError, KeyboardInterrupt):
        print("Admin creation cancelled")
        return 1
    except ValueError as error:
        print(f"Error: {error}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
