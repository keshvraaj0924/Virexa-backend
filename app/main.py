"""Opt-in Python API candidate. Never creates or migrates tables on startup."""

import logging
from collections import deque
from contextlib import asynccontextmanager
from time import monotonic
from uuid import UUID, uuid4

import anyio
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException

from app.core.api import error_response, success_response
from app.core.auth_context import AuthContext, get_auth_context, require_capability
from app.core.authentication import AuthenticationService
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.policy import Action, capabilities
from app.core.rbac import Role
from app.db.base import get_db_session
from app.db.models import Organization
from app.domain.auth.permissions import WIRE_ROLE_PERMISSIONS
from app.domain.auth.schemas import LoginRequest, RegisterRequest
from app.domain.auth.service import issue_session, register, session_payload
from app.domain.auth.sessions import router as sessions_router
from app.domain.documents.routes import router as documents_router
from app.domain.documents.storage import QuarantineStorage

logger = logging.getLogger(__name__)


class AuthRateLimiter:
    """Bounded process-local guard; a shared limiter is required for replicas."""

    def __init__(self):
        self.attempts: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        now = monotonic()
        for ip in list(self.attempts):
            queue = self.attempts[ip]
            while queue and queue[0] <= now - 300:
                queue.popleft()
            if not queue:
                del self.attempts[ip]
        if key not in self.attempts and len(self.attempts) >= 10000:
            raise ApiError(429, "RATE_LIMITED", "Too many authentication requests.")
        queue = self.attempts.setdefault(key, deque())
        if len(queue) >= 10:
            raise ApiError(429, "RATE_LIMITED", "Too many authentication requests.")
        queue.append(now)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.quarantine_storage = (
        QuarantineStorage(settings.document_storage_root)
        if settings.document_storage_root
        else None
    )
    app.state.upload_slots = anyio.CapacityLimiter(4)
    app.state.upload_timeout_seconds = 65
    app.include_router(documents_router, prefix=settings.api_prefix)
    app.include_router(sessions_router, prefix=settings.api_prefix)
    limiter = AuthRateLimiter()
    authentication = AuthenticationService()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["Retry-After", "X-Request-ID"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = str(uuid4())
        if request.url.path.startswith(settings.api_prefix + "/documents") and request.method in {
            "POST",
            "PATCH",
        }:
            body = bytearray()
            try:
                with anyio.fail_after(10):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > 32768:
                            return JSONResponse(
                                error_response(
                                    request,
                                    "REQUEST_TOO_LARGE",
                                    "Document metadata is limited to 32 KiB.",
                                ),
                                status_code=413,
                                headers={
                                    "Cache-Control": "private, no-store",
                                    "X-Request-ID": request.state.request_id,
                                },
                            )
                        body.extend(chunk)
            except TimeoutError:
                return JSONResponse(
                    error_response(
                        request, "REQUEST_TIMEOUT", "Document metadata request timed out."
                    ),
                    status_code=408,
                    headers={
                        "Cache-Control": "private, no-store",
                        "X-Request-ID": request.state.request_id,
                    },
                )
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith(settings.api_prefix):
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        headers = {"Retry-After": "300"} if exc.status == 429 else None
        return JSONResponse(
            error_response(request, exc.code, exc.message), status_code=exc.status, headers=headers
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND"}
        return JSONResponse(
            error_response(request, codes.get(exc.status_code, "INVALID_REQUEST"), str(exc.detail)),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields: dict[str, list[str]] = {}
        for error in exc.errors():
            key = str(error["loc"][-1])
            fields.setdefault(key, []).append("Invalid value.")
        return JSONResponse(
            error_response(request, "VALIDATION_ERROR", "Request is invalid.", fields),
            status_code=400,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        # Do not log SQL parameters, credentials, or exception strings.
        logger.error(
            "Database request failed request_id=%s type=%s",
            request.state.request_id,
            type(exc).__name__,
        )
        return JSONResponse(
            error_response(request, "DEPENDENCY_UNAVAILABLE", "A required service is unavailable."),
            status_code=503,
        )

    def trusted_origin(request: Request):
        if request.headers.get("origin") not in settings.origins:
            raise ApiError(403, "UNTRUSTED_ORIGIN", "Request origin is not trusted.")

    def auth_attempt(request: Request):
        trusted_origin(request)
        limiter.check(request.client.host if request.client else "unknown")

    def set_cookie(response: Response, token: str):
        response.set_cookie(
            settings.session_cookie_name,
            token,
            httponly=True,
            secure=settings.environment == "production",
            samesite="lax",
            path="/",
            max_age=settings.session_ttl_seconds,
        )

    @app.get("/health")
    async def health(request: Request):
        return success_response(request, {"status": "ok"})

    @app.get("/ready")
    async def ready(request: Request, db: AsyncSession = Depends(get_db_session)):
        await db.execute(text("SELECT 1"))
        return success_response(request, {"status": "ready", "dependencies": {"database": "ok"}})

    @app.post(
        settings.api_prefix + "/auth/register",
        status_code=201,
        dependencies=[Depends(auth_attempt)],
    )
    async def registration(
        data: RegisterRequest,
        request: Request,
        response: Response,
        db: AsyncSession = Depends(get_db_session),
    ):
        token, payload = await register(db, data, settings.session_ttl_seconds)
        set_cookie(response, token)
        return success_response(request, payload)

    @app.post(settings.api_prefix + "/auth/login", dependencies=[Depends(auth_attempt)])
    async def login(
        data: LoginRequest,
        request: Request,
        response: Response,
        db: AsyncSession = Depends(get_db_session),
    ):
        user = await authentication.authenticate(db, str(data.email), data.password)
        if user is None:
            raise ApiError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
        if user.role not in {role.value for role in Role}:
            raise ApiError(403, "FORBIDDEN", "Invalid account role.")
        token, session = await issue_session(
            db, user, settings.session_ttl_seconds, expected_password_hash=user.password_hash
        )
        organization = await db.get(Organization, user.organization_id)
        await db.commit()
        set_cookie(response, token)
        return success_response(request, session_payload(user, organization, session))

    @app.get(settings.api_prefix + "/auth/session")
    async def session(request: Request, db: AsyncSession = Depends(get_db_session)):
        token = request.cookies.get(settings.session_cookie_name, "")
        resolved = await authentication.resolve_session(db, token)
        if resolved is None:
            raise ApiError(401, "UNAUTHENTICATED", "Authentication is required.")
        user, active_session = resolved
        if user.role not in {role.value for role in Role}:
            raise ApiError(403, "FORBIDDEN", "Invalid account role.")
        organization = await db.get(Organization, user.organization_id)
        return success_response(request, session_payload(user, organization, active_session))

    @app.get(settings.api_prefix + "/me")
    async def me(request: Request, db: AsyncSession = Depends(get_db_session)):
        envelope = await session(request, db)
        payload = envelope["data"]
        role = payload["user"]["role"]
        permissions = WIRE_ROLE_PERMISSIONS.get(role)
        if permissions is None:
            raise ApiError(403, "FORBIDDEN", "Account role is not supported by this API version.")
        return success_response(request, {**payload, "permissions": permissions})

    @app.get(settings.api_prefix + "/me/permissions")
    async def effective_permissions(
        request: Request, context: AuthContext = Depends(get_auth_context)
    ):
        return success_response(request, {"capabilities": capabilities(context)})

    @app.post(settings.api_prefix + "/auth/logout", dependencies=[Depends(trusted_origin)])
    async def logout(
        request: Request, response: Response, db: AsyncSession = Depends(get_db_session)
    ):
        token = request.cookies.get(settings.session_cookie_name)
        if token:
            await authentication.revoke(db, token)
        response.delete_cookie(
            settings.session_cookie_name,
            path="/",
            httponly=True,
            secure=settings.environment == "production",
            samesite="lax",
        )
        return success_response(request, {"success": True})

    @app.get(settings.api_prefix + "/organizations/{organization_id}")
    async def organization(
        organization_id: UUID,
        request: Request,
        context: AuthContext = Depends(require_capability(Action.ORGANIZATION_READ)),
        db: AsyncSession = Depends(get_db_session),
    ):
        # Tenant predicate lives in the database query, not a post-fetch filter.
        result = await db.scalar(
            select(Organization).where(
                Organization.id == organization_id, Organization.id == context.organization_id
            )
        )
        if result is None:
            raise ApiError(404, "NOT_FOUND", "Organization was not found.")
        return success_response(request, {"id": str(result.id), "name": result.name})

    return app
