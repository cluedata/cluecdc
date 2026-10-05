import os

os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_MODE"] = "developer"
os.environ["CONNECT_SECRET_TOKEN"] = "test-service-token-with-at-least-32-characters"
os.environ["SECRET_ENCRYPTION_KEY"] = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import session_dependency  # noqa: E402
from app.main import app  # noqa: E402
from app.models.entities import Base  # noqa: E402


@pytest.fixture(autouse=True)
def pipeline_table_readiness(monkeypatch):
    class ReadySource:
        async def inspect_table(self, schema_name, table_name):
            return {
                "ready": True,
                "checks": [],
                "warnings": [],
                "primary_key_columns": ["id"],
                "estimated_rows": 100,
                "estimated_size_bytes": 8192,
            }

    async def factory(session, source):
        return ReadySource()

    monkeypatch.setattr("app.services.pipeline.source_adapter", factory)


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client(db_factory):
    async def override():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[session_dependency] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def source_payload():
    return {
        "name": "test-source",
        "type": "postgresql",
        "host": "source",
        "port": 5432,
        "database_name": "commerce",
        "username": "cdc_user",
        "password": "sensitive-test-password",
        "environment": "test",
    }
