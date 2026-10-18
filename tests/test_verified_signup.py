import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import Base
from app.db.models import Organization, Session, User
from app.domain.auth.signup import (
    CompleteSignup,
    SignupChallenge,
    StartSignup,
    VerifySignup,
    complete,
    start,
    verify,
)
from app.main import create_app

ORIGIN = {"Origin": "http://localhost:3000"}
PASSWORD = "a long unique passphrase!"


@pytest_asyncio.fixture
async def app(tmp_path):
    application = create_app(
        Settings(
            environment="test",
            signup_email_delivery="capture",
            database_url=os.environ.get(
                "SIGNUP_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/signup.db"
            ),
        )
    )
    async with application.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield application
    await application.state.engine.dispose()


def details(kind="individual", email="ada@example.com"):
    result = {"accountType": kind, "displayName": "Ada", "email": email}
    if kind == "organization":
        result["organizationName"] = "Small startup"
    return result


async def challenge(app, kind="individual", email="ada@example.com"):
    async with app.state.session_factory() as db:
        result = await start(db, StartSignup(**details(kind, email)), app.state.signup_delivery)
    return result["challengeId"], app.state.signup_delivery.messages[email]


async def proof(app, cid, code):
    async with app.state.session_factory() as db:
        return (await verify(db, VerifySignup(challengeId=cid, code=code)))["setupToken"]


async def finish(app, cid, token):
    async with app.state.session_factory() as db:
        return await complete(
            db, CompleteSignup(challengeId=cid, setupToken=token, password=PASSWORD), 3600
        )


@pytest.mark.parametrize("kind", ["individual", "organization"])
async def test_verified_http_flow_and_selector_is_not_authority(app, kind, caplog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/signup/start", json=details(kind), headers=ORIGIN)
        assert r.status_code == 202
        assert "set-cookie" not in r.headers
        cid = r.json()["data"]["challengeId"]
        code = app.state.signup_delivery.messages["ada@example.com"]
        assert code not in r.text
        assert len(code) == 32
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 0
            assert await db.scalar(select(func.count()).select_from(Session)) == 0
            row = await db.scalar(select(SignupChallenge))
            assert row.code_digest != code and row.setup_digest is None
        assert (await c.get("/api/v1/auth/session")).status_code == 401
        r = await c.post(
            "/api/v1/auth/signup/verify", json={"challengeId": cid, "code": code}, headers=ORIGIN
        )
        assert r.status_code == 200 and "set-cookie" not in r.headers
        setup = r.json()["data"]["setupToken"]
        assert (await c.get("/api/v1/auth/session")).status_code == 401
        r = await c.post(
            "/api/v1/auth/signup/complete",
            json={"challengeId": cid, "setupToken": setup, "password": PASSWORD},
            headers=ORIGIN,
        )
        assert r.status_code == 201 and "HttpOnly" in r.headers["set-cookie"]
        user = r.json()["data"]["user"]
        assert user["accountType"] == kind
        assert setup not in r.text and PASSWORD not in r.text
        assert (await c.get("/api/v1/me")).json()["data"]["user"]["accountType"] == kind
        await c.post("/api/v1/auth/logout", headers=ORIGIN)
        wrong = "organization" if kind == "individual" else "individual"
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD, "accountType": wrong},
            headers=ORIGIN,
        )
        assert r.status_code == 401 and "set-cookie" not in r.headers
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD, "accountType": kind},
            headers=ORIGIN,
        )
        assert r.status_code == 200
        async with app.state.session_factory() as db:
            user = await db.scalar(select(User))
            assert user.password_hash.startswith("$argon2id$")
            assert PASSWORD not in user.password_hash
        assert code not in caplog.text and setup not in caplog.text and PASSWORD not in caplog.text


async def test_resend_invalidates_code_and_setup_and_respects_cooldown(app):
    cid, code = await challenge(app)
    token = await proof(app, cid, code)
    with pytest.raises(ApiError) as exc:
        await challenge(app)
    assert exc.value.status == 429
    async with app.state.session_factory() as db:
        await db.execute(
            update(SignupChallenge).values(
                sent_at=datetime.now(timezone.utc) - timedelta(seconds=61)
            )
        )
        await db.commit()
    newcid, newcode = await challenge(app)
    assert newcid != cid and newcode != code
    with pytest.raises(ApiError):
        await finish(app, cid, token)
    with pytest.raises(ApiError):
        await proof(app, newcid, code)
    newtoken = await proof(app, newcid, newcode)
    await finish(app, newcid, newtoken)
    with pytest.raises(ApiError):
        await finish(app, newcid, newtoken)


async def test_attempts_expiry_and_duplicate_account(app):
    cid, code = await challenge(app)
    for _ in range(5):
        with pytest.raises(ApiError):
            await proof(app, cid, "wrong")
    with pytest.raises(ApiError):
        await proof(app, cid, code)
    async with app.state.session_factory() as db:
        assert (await db.scalar(select(SignupChallenge))).attempts == 5
    cid, code = await challenge(app, email="grace@example.com")
    async with app.state.session_factory() as db:
        await db.execute(
            update(SignupChallenge).values(
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
            )
        )
        await db.commit()
    with pytest.raises(ApiError):
        await proof(app, cid, code)
    cid, code = await challenge(app, email="owner@example.com")
    token = await proof(app, cid, code)
    await finish(app, cid, token)
    async with app.state.session_factory() as db:
        await db.execute(
            update(SignupChallenge).values(
                sent_at=datetime.now(timezone.utc) - timedelta(seconds=61)
            )
        )
        await db.commit()
    cid2, code2 = await challenge(app, email="owner@example.com")
    token2 = await proof(app, cid2, code2)
    with pytest.raises(ApiError):
        await finish(app, cid2, token2)
    async with app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(Organization)) == 1


async def test_default_disabled_delivery_and_legacy_bypass(tmp_path):
    app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/disabled.db"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/signup/start", json=details(), headers=ORIGIN)
        assert r.status_code == 503 and "challengeId" not in r.text
        r = await c.post(
            "/api/v1/auth/register",
            json={
                "displayName": "Ada",
                "email": "ada@example.com",
                "organizationName": "Org",
                "password": PASSWORD,
            },
            headers=ORIGIN,
        )
        assert r.status_code == 410 and "set-cookie" not in r.headers
    await app.state.engine.dispose()
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            database_url="sqlite://",
            allow_legacy_registration=True,
            allowed_origins="https://test.example",
        )
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            database_url="sqlite://",
            signup_email_delivery="capture",
            allowed_origins="https://test.example",
        )


async def test_delivery_failure_rolls_back(app):
    class Failed:
        def ready(self):
            pass

        async def send(self, email, code):
            raise RuntimeError("secret provider details")

    async with app.state.session_factory() as db:
        with pytest.raises(ApiError) as exc:
            await start(db, StartSignup(**details()), Failed())
        assert "secret" not in exc.value.message
        assert await db.scalar(select(func.count()).select_from(SignupChallenge)) == 0


@pytest.mark.skipif(
    not os.getenv("SIGNUP_TEST_DATABASE_URL"), reason="PostgreSQL row locks required"
)
async def test_postgres_same_code_and_setup_are_single_use(app):
    cid, code = await challenge(app)
    results = await asyncio.gather(
        proof(app, cid, code), proof(app, cid, code), return_exceptions=True
    )
    assert sum(isinstance(r, str) for r in results) == 1
    token = next(r for r in results if isinstance(r, str))
    results = await asyncio.gather(
        finish(app, cid, token), finish(app, cid, token), return_exceptions=True
    )
    assert sum(isinstance(r, tuple) for r in results) == 1
    assert sum(isinstance(r, ApiError) for r in results) == 1
    async with app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(Session)) == 1


@pytest.mark.skipif(
    not os.getenv("SIGNUP_TEST_DATABASE_URL"), reason="PostgreSQL row locks required"
)
async def test_postgres_concurrent_start_and_failed_attempts(app):
    results = await asyncio.gather(challenge(app), challenge(app), return_exceptions=True)
    assert sum(isinstance(r, tuple) for r in results) == 1
    cid, code = next(r for r in results if isinstance(r, tuple))
    results = await asyncio.gather(
        *(proof(app, cid, "wrong") for _ in range(8)), return_exceptions=True
    )
    assert all(isinstance(r, ApiError) for r in results)
    async with app.state.session_factory() as db:
        assert (await db.scalar(select(SignupChallenge))).attempts == 5
    with pytest.raises(ApiError):
        await proof(app, cid, code)


async def test_individual_and_organization_remain_separate_tenants(app):
    cid, code = await challenge(app)
    personal_token, personal = await finish(app, cid, await proof(app, cid, code))
    cid, code = await challenge(app, "organization", "startup@example.com")
    _, organization = await finish(app, cid, await proof(app, cid, code))
    first = personal["user"]["organizationId"]
    second = organization["user"]["organizationId"]
    assert first != second
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"virexa_session": personal_token},
    ) as c:
        assert (await c.get(f"/api/v1/organizations/{first}")).status_code == 200
        assert (
            await c.get(f"/api/v1/organizations/{second}", headers={"X-Tenant-ID": second})
        ).status_code == 404


async def test_input_cannot_assert_verification_or_change_account_type(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for extra in (
            {"verified": True},
            {"organizationEmail": "work@example.com"},
            {"organizationName": "Wrong"},
        ):
            r = await c.post(
                "/api/v1/auth/signup/start", json={**details(), **extra}, headers=ORIGIN
            )
            assert r.status_code == 400
        r = await c.post(
            "/api/v1/auth/signup/start",
            json={"accountType": "organization", "displayName": "Ada", "email": "ada@example.com"},
            headers=ORIGIN,
        )
        assert r.status_code == 400
        r = await c.post("/api/v1/auth/signup/start", json=details())
        assert r.status_code == 403


@pytest.mark.skipif(
    not os.getenv("SIGNUP_TEST_DATABASE_URL"), reason="PostgreSQL row locks required"
)
async def test_expiry_is_rechecked_after_waiting_for_database_lock(app):
    cid, code = await challenge(app)
    async with app.state.session_factory() as holder:
        row = await holder.scalar(select(SignupChallenge).with_for_update())
        waiter = asyncio.create_task(proof(app, cid, code))
        await asyncio.sleep(0.1)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await holder.commit()
        with pytest.raises(ApiError):
            await waiter


@pytest.mark.skipif(
    not os.getenv("SIGNUP_TEST_DATABASE_URL"), reason="PostgreSQL row locks required"
)
async def test_resend_invalidates_a_queued_verification(app):
    cid, code = await challenge(app)
    async with app.state.session_factory() as holder:
        row = await holder.scalar(select(SignupChallenge).with_for_update())
        waiter = asyncio.create_task(proof(app, cid, code))
        await asyncio.sleep(0.1)
        row.sent_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        # Same transaction rotates the challenge while the verify request waits.
        await start(holder, StartSignup(**details()), app.state.signup_delivery)
        with pytest.raises(ApiError):
            await waiter


@pytest.mark.parametrize(
    "password",
    [
        "            ",
        "aaaaaaaaaaaa",
        "123456789012",
        "abcdabcdabcd",
        "qwertyuiopasdf",
        "password123456",
        "short",
    ],
)
def test_weak_password_rejected(password):
    from uuid import uuid4

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompleteSignup(challengeId=uuid4(), setupToken="opaque", password=password)


def test_long_passphrase_allowed_without_arbitrary_character_classes():
    from uuid import uuid4

    CompleteSignup(
        challengeId=uuid4(), setupToken="opaque", password="four unusual words make memories"
    )


async def test_configured_smtp_requires_tls_and_redacts_delivery_errors(app, monkeypatch):
    import smtplib
    import ssl

    from app.domain.auth.signup import SmtpDelivery

    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, context):
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            calls.append("tls")

        def login(self, username, password):
            assert calls[-1] == "tls"
            calls.append("login")

        def send_message(self, message):
            assert calls[-1] == "login"
            calls.append("send")
            raise smtplib.SMTPDataError(550, b"synthetic private provider failure")

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings = Settings(
        database_url="sqlite://",
        signup_email_delivery="smtp",
        signup_smtp_host="smtp.example.invalid",
        signup_smtp_sender="sender@example.com",
        signup_smtp_username="synthetic-only",
        signup_smtp_password="not-a-real-secret",
    )
    assert "not-a-real-secret" not in repr(settings)
    async with app.state.session_factory() as db:
        with pytest.raises(ApiError) as exc:
            await start(db, StartSignup(**details()), SmtpDelivery(settings))
        assert exc.value.code == "EMAIL_DELIVERY_UNAVAILABLE"
        assert "private" not in exc.value.message
        assert await db.scalar(select(func.count()).select_from(SignupChallenge)) == 0
    assert calls == ["connect", "tls", "login", "send"]
