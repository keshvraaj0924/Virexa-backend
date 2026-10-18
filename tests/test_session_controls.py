# ruff: noqa: F811
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from test_fastapi_auth import ORIGIN, app, client, signup  # noqa: F401

from app.core.security import session_token_digest
from app.db.models import Session, User

BASE = "/api/v1/auth/sessions"


async def extra_session(application, token=None, **overrides):
    token = token or str(uuid4())
    async with application.state.session_factory() as db:
        owner = await db.scalar(select(User).order_by(User.email).limit(1))
        values = dict(
            user_id=owner.id,
            organization_id=owner.organization_id,
            token_digest=session_token_digest(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        values.update(overrides)
        row = Session(**values)
        db.add(row)
        await db.commit()
        return row.id, token


@pytest.mark.asyncio
async def test_inventory_exact_safe_contract_and_pagination(app):
    async with client(app) as c:
        await signup(c)
        token = c.cookies.get("virexa_session")
        for _ in range(5):
            await extra_session(app)
        await extra_session(app, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        await extra_session(app, revoked_at=datetime.now(timezone.utc))
        items, cursor = [], None
        while True:
            response = await c.get(
                BASE, params={"limit": 2, **({"cursor": cursor} if cursor else {})}
            )
            assert response.status_code == 200, response.text
            assert "no-store" in response.headers["cache-control"]
            assert token not in response.text and session_token_digest(token) not in response.text
            payload = response.json()
            items.extend(payload["data"])
            cursor = payload["meta"]["pagination"]["nextCursor"]
            if cursor is None:
                break
        assert len(items) == len({item["id"] for item in items}) == 6
        assert sum(item["current"] for item in items) == 1
        assert [item["id"] for item in items] == sorted(item["id"] for item in items)
        for item in items:
            assert set(item) == {"id", "createdAt", "expiresAt", "current"}
            assert datetime.fromisoformat(item["expiresAt"]).tzinfo is not None
        for query in ["?limit=0", "?limit=101", "?limit=bad", "?cursor=bad"]:
            assert (await c.get(BASE + query)).status_code == 400


@pytest.mark.asyncio
async def test_owner_and_tenant_scoped_inventory_revocation(app):
    async with client(app) as a, client(app) as b:
        first = (await signup(a)).json()["data"]["user"]
        await signup(b, "bob@example.com")
        foreign = (await b.get(BASE)).json()["data"][0]["id"]
        assert foreign not in (await a.get(BASE)).text
        missing = await a.delete(BASE + "/" + str(uuid4()), headers=ORIGIN)
        denied = await a.delete(BASE + "/" + foreign, headers=ORIGIN)
        assert denied.status_code == missing.status_code == 404
        assert denied.json()["error"]["message"] == missing.json()["error"]["message"]
        # Same tenant is still a different owner; no administrative bypass.
        async with app.state.session_factory() as db:
            bob = await db.scalar(select(User).where(User.email == "bob@example.com"))
            # UUID column requires a UUID instance.
            from uuid import UUID

            bob.organization_id = UUID(first["organizationId"])
            await db.execute(
                update(Session)
                .where(Session.user_id == bob.id)
                .values(organization_id=bob.organization_id)
            )
            await db.commit()
        assert foreign not in (await a.get(BASE)).text
        assert (await a.delete(BASE + "/" + foreign, headers=ORIGIN)).status_code == 404
        assert (await a.post(BASE + "/revoke-others", headers=ORIGIN)).json()["data"] == {
            "revokedCount": 0
        }
        assert (await b.get(BASE)).status_code == 200


@pytest.mark.asyncio
async def test_revoke_other_preserves_cookie_revoked_replay_and_repeat(app):
    async with client(app) as c, client(app) as replay:
        await signup(c)
        current_token = c.cookies.get("virexa_session")
        _, other_token = await extra_session(app)
        await extra_session(app)
        expired, _ = await extra_session(
            app, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        revoked, _ = await extra_session(app, revoked_at=datetime.now(timezone.utc))
        response = await c.post(BASE + "/revoke-others", headers=ORIGIN)
        assert response.json()["data"] == {"revokedCount": 2}
        assert "set-cookie" not in response.headers
        assert c.cookies.get("virexa_session") == current_token
        assert (await c.post(BASE + "/revoke-others", headers=ORIGIN)).json()["data"] == {
            "revokedCount": 0
        }
        for target in (expired, revoked):
            assert (await c.delete(f"{BASE}/{target}", headers=ORIGIN)).status_code == 404
        replay.cookies.set("virexa_session", other_token)
        for method, path in [
            ("get", BASE),
            ("get", "/api/v1/me"),
            ("post", BASE + "/revoke-others"),
            ("delete", f"{BASE}/{expired}"),
        ]:
            assert (await getattr(replay, method)(path, headers=ORIGIN)).status_code == 401
        assert (await c.get("/api/v1/auth/session")).status_code == 200


@pytest.mark.asyncio
async def test_targeted_current_clears_cookie_and_replay(app):
    async with client(app) as c:
        await signup(c)
        token = c.cookies.get("virexa_session")
        current = (await c.get(BASE)).json()["data"][0]["id"]
        response = await c.delete(f"{BASE}/{current}", headers=ORIGIN)
        assert response.json()["data"] == {"success": True}
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert c.cookies.get("virexa_session") is None
        c.cookies.set("virexa_session", token)
        assert (await c.delete(f"{BASE}/{current}", headers=ORIGIN)).status_code == 401
        assert (await c.get(BASE)).status_code == 401
        # Existing logout remains idempotent for already revoked cookies.
        assert (await c.post("/api/v1/auth/logout", headers=ORIGIN)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, "null", "https://evil.example"])
async def test_mutation_origin_and_anonymous_boundaries(app, origin):
    async with client(app) as c:
        assert (await c.get(BASE)).status_code == 401
        await signup(c)
        current = (await c.get(BASE)).json()["data"][0]["id"]
        headers = {} if origin is None else {"Origin": origin}
        for method, path in [("post", BASE + "/revoke-others"), ("delete", f"{BASE}/{current}")]:
            response = await getattr(c, method)(path, headers=headers)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "UNTRUSTED_ORIGIN"
        assert (await c.get(BASE)).status_code == 200


@pytest.mark.asyncio
async def test_targeted_repeat_and_concurrent_revocation(app):
    async with client(app) as c:
        await signup(c)
        target, token = await extra_session(app)
        responses = await asyncio.gather(
            *[c.delete(f"{BASE}/{target}", headers=ORIGIN) for _ in range(2)]
        )
        assert sorted(r.status_code for r in responses) == [200, 404]
        for _ in range(4):
            await extra_session(app)
        responses = await asyncio.gather(
            *[c.post(BASE + "/revoke-others", headers=ORIGIN) for _ in range(2)]
        )
        assert all(r.status_code == 200 for r in responses)
        assert sorted(r.json()["data"]["revokedCount"] for r in responses) == [0, 4]


@pytest.mark.asyncio
async def test_mutual_revocation_serialized_with_live_authority(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock semantics; executed in PostgreSQL 16 CI")
    async with client(app) as a, client(app) as b:
        await signup(a)
        original = (await a.get(BASE)).json()["data"][0]["id"]
        other, token = await extra_session(app)
        b.cookies.set("virexa_session", token)
        responses = await asyncio.wait_for(
            asyncio.gather(
                a.delete(f"{BASE}/{other}", headers=ORIGIN),
                b.delete(f"{BASE}/{original}", headers=ORIGIN),
            ),
            timeout=10,
        )
        assert sorted(r.status_code for r in responses) == [200, 401]
        statuses = [(await a.get(BASE)).status_code, (await b.get(BASE)).status_code]
        assert sorted(statuses) == [200, 401]


@pytest.mark.asyncio
async def test_delete_cors_preflight(app):
    async with client(app) as c:
        response = await c.options(
            BASE + "/" + str(uuid4()),
            headers={
                **ORIGIN,
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "content-type,x-request-id",
            },
        )
        assert response.status_code == 200
        assert "DELETE" in response.headers["access-control-allow-methods"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["expired", "revoked", "moved", "invalid_role"])
async def test_every_session_route_rechecks_server_authority(app, state):
    from app.db.models import Organization

    async with client(app) as c:
        await signup(c)
        current = (await c.get(BASE)).json()["data"][0]["id"]
        async with app.state.session_factory() as db:
            if state == "moved":
                other = Organization(name="Other")
                db.add(other)
                await db.flush()
                await db.execute(update(User).values(organization_id=other.id))
            elif state == "invalid_role":
                await db.execute(update(User).values(role="invalid"))
            else:
                field = "expires_at" if state == "expired" else "revoked_at"
                await db.execute(
                    update(Session).values(
                        **{field: datetime.now(timezone.utc) - timedelta(seconds=1)}
                    )
                )
            await db.commit()
        expected = 403 if state == "invalid_role" else 401
        for method, path in [
            ("get", BASE),
            ("post", BASE + "/revoke-others"),
            ("delete", f"{BASE}/{current}"),
        ]:
            assert (await getattr(c, method)(path, headers=ORIGIN)).status_code == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tenant", "invalid_role", "password"])
async def test_login_refreshes_authority_before_issuance(app, monkeypatch, change):
    from test_fastapi_auth import PASSWORD

    from app.db.models import Organization
    from app.domain.auth.service import issue_session as original_issue

    async with client(app) as c:
        await signup(c)
        async with app.state.session_factory() as db:
            other = Organization(name="New tenant")
            db.add(other)
            await db.commit()
            other_id = other.id

        async def race(db, user, ttl, **kwargs):
            # Deterministic state change after password/role check and before issuance lock.
            async with app.state.session_factory() as writer:
                values = (
                    {"organization_id": other_id} if change == "tenant" else {"role": "invalid"}
                )
                if change == "password":
                    values = {"password_hash": "different-credential-hash"}
                await writer.execute(update(User).where(User.id == user.id).values(**values))
                await writer.commit()
            return await original_issue(db, user, ttl, **kwargs)

        monkeypatch.setattr("app.main.issue_session", race)
        response = await c.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "ada@example.com", "password": PASSWORD},
        )
        if change == "tenant":
            assert response.status_code == 200
            assert response.json()["data"]["user"]["organizationId"] == str(other_id)
            assert response.json()["data"]["user"]["organizationName"] == "New tenant"
            assert (await c.get(BASE)).status_code == 200
        else:
            assert response.status_code == (401 if change == "password" else 403)
            assert "set-cookie" not in response.headers
            async with app.state.session_factory() as db:
                assert len(list(await db.scalars(select(Session)))) == 1
