# ruff: noqa: F811
import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_document_intake import app, client, create, finish, put, signup, start  # noqa: F401

from app.core.errors import ApiError
from app.core.rbac import Role
from app.db.models import User
from app.domain.usage.provider import ProviderTokens
from app.domain.usage.service import period, reserve, settle


async def test_completion_meter_exactly_once_and_unknown_tokens(app):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        assert (await finish(c, doc, upload)).status_code == 409
        assert (await c.get("/api/v1/usage/me/history")).json()["data"]["items"] == []
        assert (await put(c, upload)).status_code == 204
        assert (await finish(c, doc, upload)).status_code == 200
        assert (await finish(c, doc, upload)).json()["data"]["replayed"]
        summary = (await c.get("/api/v1/usage/me")).json()["data"]
        assert summary["meters"]["document_uploads"]["used"] == 1
        assert summary["meters"]["input_tokens"]["used"] is None
        assert summary["cost"]["amount"] is None
        assert summary["subscription"]["status"] == "preview"
        assert summary["subscription"]["limits"]["document_uploads"]["remaining"] == 999
        history = (await c.get("/api/v1/usage/me/history")).json()["data"]
        assert len(history["items"]) == 1
        assert history["items"][0]["state"] == "succeeded"
        assert (await c.post("/api/v1/usage/me", json={"input_tokens": 100})).status_code == 405


async def add(db, user, key, state="succeeded"):
    row = await reserve(
        db,
        organization_id=user.organization_id,
        user_id=user.id,
        key=key,
        operation="document_intake",
        meter="document_uploads",
        quantity=1,
    )
    await settle(db, organization_id=user.organization_id, event_id=row.id, state=state)
    await db.commit()
    return row


async def test_scope_roles_and_keyset_filters(app):
    async with client(app) as c, client(app) as other:
        identity = await signup(c)
        await signup(other, "other@example.com")
        async with app.state.session_factory() as db:
            user = await db.scalar(select(User).where(User.id == UUID(identity["id"])))
            coworker = User(
                id=uuid4(),
                organization_id=user.organization_id,
                display_name="Coworker",
                email="co@example.com",
                password_hash="unused",
                role="viewer",
            )
            db.add(coworker)
            await db.flush()
            await add(db, user, "one")
            await add(db, user, "two", "failed")
            await add(db, coworker, "three")
        first = (await c.get("/api/v1/usage/me/history?limit=1")).json()["data"]
        params = {
            "limit": 1,
            "cursor": first["nextCursor"],
            "start": first["start"],
            "end": first["end"],
        }
        second = (await c.get("/api/v1/usage/me/history", params=params)).json()["data"]
        assert second["items"][0]["id"] != first["items"][0]["id"]
        assert second["nextCursor"] is None
        assert (await other.get("/api/v1/usage/me/history", params=params)).status_code == 400
        assert (
            await c.get("/api/v1/usage/me/history", params={**params, "state": "failed"})
        ).status_code == 400
        assert (await other.get("/api/v1/usage/organization")).json()["data"]["meters"][
            "document_uploads"
        ]["used"] == 0
        assert len((await c.get("/api/v1/usage/organization/history")).json()["data"]["items"]) == 3
        for role in Role:
            async with app.state.session_factory() as db:
                user = await db.get(User, UUID(identity["id"]))
                user.role = role.value
                await db.commit()
            assert (await c.get("/api/v1/usage/me")).status_code == 200
            expected = (
                200
                if role
                in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN, Role.AUDITOR}
                else 403
            )
            assert (await c.get("/api/v1/usage/organization")).status_code == expected
        assert (
            await c.get(
                "/api/v1/usage/me",
                params={"start": "2026-01-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
            )
        ).status_code == 400
        assert (await c.get("/api/v1/usage/me/history?cursor=garbage")).status_code == 400
        assert (await c.get("/api/v1/usage/me/history?limit=101")).status_code == 400
    async with client(app) as anonymous:
        assert (await anonymous.get("/api/v1/usage/me")).status_code == 401


async def test_reservation_lifecycle_quota_replay_and_tenant_guard(app):
    async with client(app) as c:
        identity = await signup(c)
    async with app.state.session_factory() as db:
        user = await db.get(User, UUID(identity["id"]))
        p = await period(db, user.organization_id)
        p.upload_limit = 1
        await db.commit()
        row = await reserve(
            db,
            organization_id=user.organization_id,
            user_id=user.id,
            key="hold",
            operation="document_intake",
            meter="document_uploads",
            quantity=1,
        )
        assert (
            await reserve(
                db,
                organization_id=user.organization_id,
                user_id=user.id,
                key="hold",
                operation="document_intake",
                meter="document_uploads",
                quantity=1,
            )
        ).id == row.id
        with pytest.raises(ApiError) as error:
            await reserve(
                db,
                organization_id=user.organization_id,
                user_id=user.id,
                key="over",
                operation="document_intake",
                meter="document_uploads",
                quantity=1,
            )
        assert error.value.code == "USAGE_LIMIT_EXCEEDED"
        await settle(db, organization_id=user.organization_id, event_id=row.id, state="released")
        with pytest.raises(ApiError) as error:
            await settle(
                db, organization_id=user.organization_id, event_id=row.id, state="succeeded"
            )
        assert error.value.code == "USAGE_ALREADY_SETTLED"
        with pytest.raises(ApiError):
            await settle(db, organization_id=uuid4(), event_id=row.id, state="released")
        with pytest.raises(ApiError):
            await reserve(
                db,
                organization_id=user.organization_id,
                user_id=uuid4(),
                key="foreign",
                operation="document_intake",
                meter="document_uploads",
                quantity=1,
            )
        await add(db, user, "replacement")
        with pytest.raises(ApiError) as error:
            await reserve(
                db,
                organization_id=user.organization_id,
                user_id=user.id,
                key="tokens",
                operation="ai",
                meter="input_tokens",
                quantity=1,
            )
        assert error.value.code == "METER_UNAVAILABLE"


async def test_postgres_concurrent_last_allowance(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Row locks require PostgreSQL")
    async with client(app) as c:
        identity = await signup(c)
    async with app.state.session_factory() as db:
        user = await db.get(User, UUID(identity["id"]))
        org, uid = user.organization_id, user.id
        p = await period(db, org)
        p.upload_limit = 1
        await db.commit()

    async def contender(key):
        async with app.state.session_factory() as db:
            try:
                row = await reserve(
                    db,
                    organization_id=org,
                    user_id=uid,
                    key=key,
                    operation="document_intake",
                    meter="document_uploads",
                    quantity=1,
                )
                await settle(db, organization_id=org, event_id=row.id, state="succeeded")
                await db.commit()
                return "ok"
            except ApiError as e:
                return e.code

    assert sorted(await asyncio.gather(contender("a"), contender("b"))) == [
        "USAGE_LIMIT_EXCEEDED",
        "ok",
    ]


def test_provider_token_measurement_validation():
    assert ProviderTokens(10, 2, 3).cached_tokens == 3
    for values in ((-1, 0, 0), (1, 0, 2), (True, 0, 0), (1.5, 0, 0)):
        with pytest.raises(ValueError):
            ProviderTokens(*values)


async def test_reservation_uses_one_timestamp_at_month_boundary(app, monkeypatch):
    from datetime import datetime, timezone

    from app.domain.usage import service
    from app.domain.usage.models import SubscriptionPeriod

    async with client(app) as c:
        identity = await signup(c)
    times = iter(
        [
            datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc),
            datetime(2026, 2, 1, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(service, "now", lambda: next(times))
    async with app.state.session_factory() as db:
        user = await db.get(User, UUID(identity["id"]))
        row = await reserve(
            db,
            organization_id=user.organization_id,
            user_id=user.id,
            key="midnight",
            operation="document_intake",
            meter="document_uploads",
            quantity=1,
        )
        current = await db.get(SubscriptionPeriod, row.period_id)
        assert current.starts_at.month == row.created_at.month == 1
        assert row.created_at == row.updated_at


async def test_settlement_refreshes_retained_identity_after_other_session(app):
    async with client(app) as c:
        identity = await signup(c)
    async with app.state.session_factory() as a:
        user = await a.get(User, UUID(identity["id"]))
        row = await reserve(
            a,
            organization_id=user.organization_id,
            user_id=user.id,
            key="held",
            operation="document_intake",
            meter="document_uploads",
            quantity=1,
        )
        await a.commit()
        async with app.state.session_factory() as b:
            await settle(b, organization_id=user.organization_id, event_id=row.id, state="released")
            await b.commit()
        with pytest.raises(ApiError) as error:
            await settle(
                a, organization_id=user.organization_id, event_id=row.id, state="succeeded"
            )
        assert error.value.code == "USAGE_ALREADY_SETTLED"


@pytest.mark.parametrize("quantity", [True, 1.5, -1])
async def test_reserve_rejects_non_integer_quantity(app, quantity):
    async with app.state.session_factory() as db:
        with pytest.raises(ValueError):
            await reserve(
                db,
                organization_id=uuid4(),
                user_id=uuid4(),
                key="bad",
                operation="document_intake",
                meter="document_uploads",
                quantity=quantity,
            )


async def test_postgres_concurrent_replay_and_terminal_settlement(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Row locks require PostgreSQL")
    async with client(app) as c:
        identity = await signup(c)
    async with app.state.session_factory() as db:
        user = await db.get(User, UUID(identity["id"]))
        org, uid = user.organization_id, user.id

    async def hold():
        async with app.state.session_factory() as db:
            row = await reserve(
                db,
                organization_id=org,
                user_id=uid,
                key="same",
                operation="document_intake",
                meter="document_uploads",
                quantity=1,
            )
            await db.commit()
            return row.id

    one, two = await asyncio.gather(hold(), hold())
    assert one == two

    async def terminal(state):
        async with app.state.session_factory() as db:
            try:
                await settle(db, organization_id=org, event_id=one, state=state)
                await db.commit()
                return "ok"
            except ApiError as error:
                return error.code

    results = await asyncio.gather(terminal("succeeded"), terminal("released"))
    assert sorted(results) == ["USAGE_ALREADY_SETTLED", "ok"]
