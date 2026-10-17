import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.core.config import Settings
from app.core.security import session_token_digest
from app.db.base import Base
from app.db.models import Organization, Session, User
from app.main import create_app

ORIGIN = {"Origin": "http://localhost:3000"}
PASSWORD = "a-real-long-test-password"


@pytest_asyncio.fixture
async def app(tmp_path):
    application = create_app(
        Settings(
            database_url=os.environ.get(
                "TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db"
            )
        )
    )
    async with application.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield application
    await application.state.engine.dispose()


def client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def signup(c, email="ada@example.com"):
    return await c.post(
        "/api/v1/auth/register",
        headers=ORIGIN,
        json={
            "displayName": "Ada Lovelace",
            "email": email,
            "password": PASSWORD,
            "organizationName": "Example Operations",
        },
    )


@pytest.mark.asyncio
async def test_real_signup_login_session_logout_contract(app):
    async with client(app) as c:
        response = await signup(c)
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert data["user"]["role"] == "admin"
        assert data["user"]["organizationName"] == "Example Operations"
        assert "expiresAt" in data
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"]
        token = c.cookies.get("virexa_session")
        async with app.state.session_factory() as db:
            user = await db.scalar(select(User))
            stored = await db.scalar(select(Session))
            assert user.password_hash.startswith("$argon2")
            assert PASSWORD not in user.password_hash
            assert stored.token_digest == session_token_digest(token)
            assert stored.organization_id == user.organization_id
        assert (await c.get("/api/v1/auth/session")).status_code == 200
        assert (await c.post("/api/v1/auth/logout", headers=ORIGIN)).status_code == 200
        c.cookies.set("virexa_session", token)
        assert (await c.get("/api/v1/auth/session")).status_code == 401
        response = await c.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "ADA@example.com", "password": PASSWORD},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_cross_tenant_id_and_header_cannot_change_scope(app):
    async with client(app) as a, client(app) as b:
        first = (await signup(a)).json()["data"]["user"]
        second = (await signup(b, "grace@example.com")).json()["data"]["user"]
        own = await a.get(f"/api/v1/organizations/{first['organizationId']}")
        assert own.status_code == 200
        foreign = await a.get(
            f"/api/v1/organizations/{second['organizationId']}",
            headers={"X-Tenant-ID": second["organizationId"]},
        )
        missing = await a.get(f"/api/v1/organizations/{uuid4()}")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
        assert second["organizationId"] not in foreign.text
        assert "no-store" in foreign.headers["cache-control"]


@pytest.mark.asyncio
async def test_database_role_change_takes_effect_and_no_platform_bypass(app):
    async with client(app) as c:
        user = (await signup(c)).json()["data"]["user"]
        url = f"/api/v1/organizations/{user['organizationId']}"
        async with app.state.session_factory() as db:
            await db.execute(update(User).values(role="viewer"))
            await db.commit()
        assert (await c.get(url)).status_code == 403
        async with app.state.session_factory() as db:
            await db.execute(update(User).values(role="platform_admin"))
            other = Organization(name="Other")
            db.add(other)
            await db.commit()
        assert (await c.get(f"/api/v1/organizations/{other.id}")).status_code == 404


@pytest.mark.asyncio
async def test_session_cannot_follow_user_between_tenants(app):
    async with client(app) as c:
        await signup(c)
        async with app.state.session_factory() as db:
            other = Organization(name="Other")
            db.add(other)
            await db.flush()
            await db.execute(update(User).values(organization_id=other.id))
            await db.commit()
        assert (await c.get("/api/v1/auth/session")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "revoked", "invalid_role"])
async def test_invalid_server_state_fails_closed(app, change):
    async with client(app) as c:
        await signup(c)
        async with app.state.session_factory() as db:
            if change == "invalid_role":
                await db.execute(update(User).values(role="invented_admin"))
            else:
                values = (
                    {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
                    if change == "expired"
                    else {"revoked_at": datetime.now(timezone.utc)}
                )
                await db.execute(update(Session).values(**values))
            await db.commit()
        assert (await c.get("/api/v1/auth/session")).status_code == (
            403 if change == "invalid_role" else 401
        )


@pytest.mark.asyncio
async def test_normalized_email_uniqueness_rolls_back_org(app):
    async with client(app) as c:
        assert (await signup(c)).status_code == 201
        duplicate = await signup(c, "ADA@example.com")
        assert duplicate.status_code == 409
        async with app.state.session_factory() as db:
            assert len(list((await db.scalars(select(Organization))))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
async def test_csrf_rejects_missing_or_foreign_origin(app, origin):
    async with client(app) as c:
        headers = {} if origin is None else {"Origin": origin}
        response = await c.post(
            "/api/v1/auth/login",
            headers=headers,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "UNTRUSTED_ORIGIN"


@pytest.mark.asyncio
async def test_rate_limit_and_generic_credentials(app):
    async with client(app) as c:
        for _ in range(10):
            response = await c.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "absent@example.com", "password": PASSWORD},
            )
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
        response = await c.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "absent@example.com", "password": PASSWORD},
        )
        assert response.status_code == 429
        assert response.headers["retry-after"] == "300"


@pytest.mark.asyncio
async def test_validation_does_not_echo_password_or_accept_role(app):
    async with client(app) as c:
        response = await c.post(
            "/api/v1/auth/register",
            headers=ORIGIN,
            json={
                "displayName": "  ",
                "email": "bad-email",
                "password": PASSWORD,
                "organizationName": "Example",
                "role": "platform_admin",
                "tenantId": str(uuid4()),
            },
        )
        assert response.status_code == 400
        assert PASSWORD not in response.text
        assert "role" in response.json()["error"]["fieldErrors"]
        assert response.json()["error"]["requestId"] == response.json()["meta"]["requestId"]


@pytest.mark.asyncio
async def test_production_cookie_flags(tmp_path):
    application = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/prod.db",
            environment="production",
            allowed_origins="https://virexa.example",
        )
    )
    async with application.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with client(application) as c:
        response = await c.post(
            "/api/v1/auth/register",
            headers={"Origin": "https://virexa.example"},
            json={
                "displayName": "Ada",
                "email": "ada@example.com",
                "password": PASSWORD,
                "organizationName": "Example",
            },
        )
        assert response.status_code == 201
        assert "Secure" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
    await application.state.engine.dispose()


@pytest.mark.asyncio
async def test_legacy_duplicate_email_login_is_not_arbitrarily_selected(app):
    async with client(app) as c:
        await signup(c)
        async with app.state.session_factory() as db:
            original = await db.scalar(select(User))
            other = Organization(name="Legacy duplicate")
            db.add(other)
            await db.flush()
            db.add(
                User(
                    organization_id=other.id,
                    email="ADA@example.com",
                    display_name="Ada",
                    password_hash=original.password_hash,
                    role="organization_admin",
                )
            )
            await db.commit()
        response = await c.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_concurrent_signup_one_account_one_org(app):
    import asyncio

    async with client(app) as first, client(app) as second:
        responses = await asyncio.gather(signup(first), signup(second))
        assert sorted(response.status_code for response in responses) == [201, 409]
        async with app.state.session_factory() as db:
            assert len(list(await db.scalars(select(User)))) == 1
            assert len(list(await db.scalars(select(Organization)))) == 1


@pytest.mark.asyncio
async def test_health_readiness_and_no_auth(app):
    async with client(app) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/ready")).status_code == 200
        assert (await c.get(f"/api/v1/organizations/{uuid4()}")).status_code == 401


@pytest.mark.asyncio
async def test_browser_preflight_allows_frontend_request_id(app):
    async with client(app) as c:
        response = await c.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-request-id",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_me_supports_frontend_ssr_identity_contract(app):
    async with client(app) as c:
        assert (await c.get("/api/v1/me")).status_code == 401
        registered = (await signup(c)).json()["data"]
        response = await c.get("/api/v1/me")
        assert response.status_code == 200
        assert response.json()["data"]["user"] == registered["user"]
        assert "platform:read" in response.json()["data"]["permissions"]
        assert "no-store" in response.headers["cache-control"]
