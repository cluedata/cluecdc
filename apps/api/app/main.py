import secrets
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.api import router as alert_router
from app.api.connections import router as connection_router
from app.api.destinations import router as destination_router
from app.api.routes import router
from app.core.config import get_settings
from app.core.database import Session, engine, session_dependency
from app.core.errors import DomainError
from app.core.logging import configure_logging
from app.services.secrets import secret_provider

settings = get_settings()


configure_logging("cluecdc-api")
log = structlog.get_logger(component="http")
REQUESTS = Counter("cluecdc_http_requests_total", "HTTP requests", ["method", "status"])

try:
    APP_VERSION = version("cluecdc-api") or "0.0.0+unknown"
except PackageNotFoundError:
    APP_VERSION = "0.0.0+unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    yield
    await engine.dispose()


app = FastAPI(title="ClueCDC", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    expose_headers=["X-Correlation-ID"],
)
app.include_router(router)
app.include_router(destination_router)
app.include_router(connection_router)
app.include_router(alert_router)


@app.get("/internal/secrets/{reference}", include_in_schema=False)
async def resolve_connect_secret(
    reference: UUID,
    db: Annotated[AsyncSession, Depends(session_dependency)],
    authorization: Annotated[str | None, Header()] = None,
    key: Annotated[str, Query(pattern=r"^[a-z_]{1,40}$")] = "password",
):
    expected = "Bearer " + get_settings().connect_secret_token.get_secret_value()
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise DomainError("FORBIDDEN", "Secret service authentication required", 403)
    value = await secret_provider(db).get_secret(reference)
    allowed = {
        "password",
        "access_key",
        "secret_key",
        "session_token",
        "credential",
        "token",
        "client_secret",
    }
    if key not in allowed or key not in value:
        raise DomainError("SECRET_KEY_NOT_FOUND", "Secret field was not found", 404)
    return Response(value[key], media_type="text/plain", headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = request.headers.get("X-Correlation-ID", "")
    correlation_id = (
        supplied
        if len(supplied) <= 64 and supplied.replace("-", "").isalnum()
        else str(uuid.uuid4())
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=correlation_id, correlation_id=correlation_id)
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as exc:
        frames = [
            {"file": f.filename, "line": f.lineno, "function": f.name}
            for f in traceback.extract_tb(exc.__traceback__)[-8:]
        ]
        log.error(
            "unhandled_request_error",
            method=request.method,
            path=request.url.path,
            error_type=type(exc).__name__,
            frames=frames,
        )
        response = JSONResponse(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "The operation failed; use the correlation ID "
                    "to inspect server logs",
                    "details": {},
                }
            },
            status_code=500,
        )
    response.headers["X-Correlation-ID"] = correlation_id
    REQUESTS.labels(request.method, str(response.status_code)).inc()
    log.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration=round(time.monotonic() - started, 3),
    )
    return response


@app.exception_handler(DomainError)
async def domain_error(request: Request, exc: DomainError):
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Pydantic input fields can contain secrets. Return only location and safe error type.
    details = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
    return JSONResponse(
        {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": details,
            }
        },
        status_code=422,
    )


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    return JSONResponse(
        {
            "error": {
                "code": "RESOURCE_CONFLICT",
                "message": "A resource with this name already exists or is still referenced",
                "details": {},
            }
        },
        status_code=409,
    )


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health", include_in_schema=False)
async def health():
    return await live()


@app.get("/health/ready")
async def ready():
    async with Session() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
async def readiness():
    return await ready()


@app.get("/metrics", include_in_schema=False)
async def metrics():
    # Metrics are on the private API port; no stream metrics are fabricated.
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
