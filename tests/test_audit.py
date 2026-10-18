# ruff: noqa: F811
"""Runs on SQLite and PostgreSQL 16 through DOCUMENT_TEST_DATABASE_URL."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_document_intake import (  # noqa: F401
    ORIGIN,
    app,
    client,
    create,
    finish,
    put,
    signup,
    start,
)

from app.core.rbac import Role
from app.db.models import Organization, Session, User
from app.domain.audit.models import AuditEvent
from app.domain.audit.service import record
from app.domain.documents.models import Document, Upload
from app.domain.usage.models import UsageEvent


async def events(c, **params):
    response = await c.get("/api/v1/audit/events", params=params)
    assert response.status_code == 200, response.text
    assert "no-store" in response.headers["cache-control"]
    return response.json()["data"]


async def test_actual_events_replays_and_redaction(app):
    async with client(app) as c:
        identity = await signup(c)
        doc = await create(c, originalFileName="private-bank-statement.pdf")
        attempt = await start(c, doc)
        failed = await finish(c, doc, attempt)
        assert failed.status_code == 409
        assert (await put(c, attempt)).status_code == 204
        assert (await finish(c, doc, attempt)).status_code == 200
        assert (await finish(c, doc, attempt)).json()["data"]["replayed"]
        data = await events(c)
        actions = [e["action"] for e in data["events"]]
        assert actions.count("document.intake.completed") == 1
        assert actions.count("document.intake.failed") == 1
        assert actions.count("document.created") == 1
        assert actions.count("auth.signup.completed") == 1
        assert data["coverage"]["mode"] == "forward_only"
        for event in data["events"]:
            assert event["actorUserId"] == identity["id"]
            UUID(event["requestId"])
            assert set(event["metadata"]) == {"outcome"}
        stored = str(data)
        for secret in [
            "private-bank-statement",
            "one@example.com",
            "synthetic-long-password",
            "token_digest",
            "password_hash",
        ]:
            assert secret not in stored
        assert data["events"][0]["requestId"] != "injected-client-correlation"
        for verb in ["post", "put", "delete", "patch"]:
            assert (await getattr(c, verb)("/api/v1/audit/events")).status_code == 405


async def test_tenant_roles_pagination_and_generic_denials(app):
    async with client(app) as c, client(app) as other:
        identity = await signup(c)
        other_identity = await signup(other, "other@example.com")
        foreign = await create(other)
        missing = str(uuid4())
        for target in [foreign["id"], missing]:
            denied = await c.get(f"/api/v1/documents/{target}")
            assert denied.status_code == 404
            assert denied.json()["error"]["message"] == "Document was not found."
        denied_events = (await events(c, outcome="denied"))["events"]
        assert len(denied_events) == 2
        assert all(row["resourceId"] is None for row in denied_events)
        assert foreign["id"] not in str(denied_events)
        async with app.state.session_factory() as db:
            user = await db.get(User, UUID(identity["id"]))
            coworker = User(
                id=uuid4(),
                organization_id=user.organization_id,
                display_name="Coworker",
                email="coworker@example.com",
                password_hash="unused",
                role="auditor",
            )
            db.add(coworker)
            await db.flush()
            await record(
                db,
                organization_id=user.organization_id,
                actor_user_id=coworker.id,
                action="access.denied",
                request_id=str(uuid4()),
            )
            await db.commit()
        # Equal-time ties remain ordered by unique ID.
        async with app.state.session_factory() as db:
            from sqlalchemy import update

            await db.execute(
                update(AuditEvent).values(created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
            )
            await db.commit()
        all_rows = (await events(c))["events"]
        assert all(row["organizationId"] == identity["organizationId"] for row in all_rows)
        assert len(all_rows) == 4
        assert len((await events(other))["events"]) == 2
        page = await events(c, limit=1)
        ids = [page["events"][0]["id"]]
        first_cursor = page["nextCursor"]
        while page["nextCursor"]:
            page = await events(c, limit=1, cursor=page["nextCursor"])
            ids += [row["id"] for row in page["events"]]
        assert ids == sorted(ids, reverse=True)
        assert len(set(ids)) == len(all_rows)
        for params in [
            {"cursor": first_cursor, "outcome": "denied"},
            {"cursor": "bad"},
            {"limit": 101},
            {"limit": 0},
            {"organizationId": other_identity["organizationId"]},
            {"actorUserId": identity["id"]},
            {"action": "invented"},
            {"outcome": "invented"},
        ]:
            assert (await c.get("/api/v1/audit/events", params=params)).status_code == 400
        assert (
            await other.get("/api/v1/audit/events", params={"cursor": first_cursor})
        ).status_code == 400
        for role in Role:
            async with app.state.session_factory() as db:
                user = await db.get(User, UUID(identity["id"]))
                user.role = role.value
                await db.commit()
            allowed = role in {
                Role.PLATFORM_ADMIN,
                Role.TENANT_ADMIN,
                Role.ORGANIZATION_ADMIN,
                Role.AUDITOR,
            }
            response = await c.get("/api/v1/audit/events")
            assert response.status_code == (200 if allowed else 403)
            capabilities = (await c.get("/api/v1/me/permissions")).json()["data"]["capabilities"]
            assert ("audit:read" in capabilities) == allowed
    async with client(app) as anonymous:
        assert (await anonymous.get("/api/v1/audit/events")).status_code == 401


async def test_auth_session_events_no_duplicate_revocation(app):
    async with client(app) as c, client(app) as second:
        identity = await signup(c)
        for _ in range(2):
            response = await second.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "one@example.com", "password": "synthetic-long-password"},
            )
            assert response.status_code == 200
        assert (await c.post("/api/v1/auth/sessions/revoke-others", headers=ORIGIN)).json()["data"][
            "revokedCount"
        ] == 2
        assert (await c.post("/api/v1/auth/sessions/revoke-others", headers=ORIGIN)).json()["data"][
            "revokedCount"
        ] == 0
        assert len((await events(c, action="auth.session.revoked"))["events"]) == 2
        assert len((await events(c, action="auth.login"))["events"]) == 2
        current = (await c.get("/api/v1/auth/sessions")).json()["data"][0]["id"]
        token = c.cookies.get("virexa_session")
        assert (await c.post("/api/v1/auth/logout", headers=ORIGIN)).status_code == 200
        c.cookies.set("virexa_session", token)
        assert (await c.post("/api/v1/auth/logout", headers=ORIGIN)).status_code == 200
        async with app.state.session_factory() as db:
            logs = list(
                await db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.logout"))
            )
            assert len(logs) == 1 and logs[0].resource_id == current
            assert logs[0].actor_user_id == UUID(identity["id"])


async def test_transactional_rollback_of_document_and_usage(app, monkeypatch):
    import app.domain.documents.routes as routes

    real_record = routes.record
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        attempt = await start(c, doc)
        assert (await put(c, attempt)).status_code == 204

        async def fail_after_event(*args, **kwargs):
            await real_record(*args, **kwargs)
            raise RuntimeError("Synthetic transaction interruption")

        monkeypatch.setattr(routes, "record", fail_after_event)
        with pytest.raises(RuntimeError, match="Synthetic transaction"):
            await finish(c, doc, attempt)
        async with app.state.session_factory() as db:
            stored = await db.get(Document, UUID(doc["id"]))
            assert stored.scan_status == "awaiting_upload"
            assert await db.scalar(select(func.count()).select_from(UsageEvent)) == 0
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "document.intake.completed")
                )
                == 0
            )
            upload = await db.scalar(select(Upload).where(Upload.document_id == stored.id))
            assert upload.status == "initiated"
        monkeypatch.setattr(routes, "record", real_record)
        assert (await finish(c, doc, attempt)).status_code == 200
        assert len((await events(c, action="document.intake.completed"))["events"]) == 1


async def test_actor_scope_guard_and_personal_workspace(app):
    async with client(app) as c, client(app) as other:
        identity = await signup(c)
        other_identity = await signup(other, "other@example.com")
        async with app.state.session_factory() as db:
            with pytest.raises(ValueError, match="tenant"):
                await record(
                    db,
                    organization_id=UUID(identity["organizationId"]),
                    actor_user_id=UUID(other_identity["id"]),
                    action="access.denied",
                    request_id=str(uuid4()),
                )
            with pytest.raises(ValueError, match="opaque"):
                await record(
                    db,
                    organization_id=UUID(identity["organizationId"]),
                    actor_user_id=UUID(identity["id"]),
                    action="document.created",
                    request_id=str(uuid4()),
                    resource_id="sensitive-file.pdf",
                )
            organization = await db.get(Organization, UUID(identity["organizationId"]))
            organization.account_type = "individual"
            await db.commit()
        assert len((await events(c))["events"]) == 1


async def test_login_and_revocation_roll_back_with_audit_failure(app, monkeypatch):
    import app.domain.auth.sessions as sessions
    import app.main as main

    async with client(app) as c, client(app) as other:
        await signup(c)
        async with app.state.session_factory() as db:
            before = await db.scalar(select(func.count()).select_from(Session))
        original = main.record

        async def interrupt(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("Synthetic audit interruption")

        monkeypatch.setattr(main, "record", interrupt)
        with pytest.raises(RuntimeError, match="Synthetic audit"):
            await other.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "one@example.com", "password": "synthetic-long-password"},
            )
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Session)) == before
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "auth.login")
                )
                == 0
            )
        monkeypatch.setattr(main, "record", original)
        await other.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "one@example.com", "password": "synthetic-long-password"},
        )
        monkeypatch.setattr(sessions, "record", interrupt)
        with pytest.raises(RuntimeError, match="Synthetic audit"):
            await c.post("/api/v1/auth/sessions/revoke-others", headers=ORIGIN)
        assert (await other.get("/api/v1/auth/session")).status_code == 200
        async with app.state.session_factory() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "auth.session.revoked")
                )
                == 0
            )


async def test_verified_signup_success_is_transactional_and_redacted(app, monkeypatch):
    from app.domain.auth import signup as signup_service
    from app.domain.auth.signup import SyntheticCaptureDelivery

    app.state.signup_delivery = SyntheticCaptureDelivery()
    async with client(app) as c:
        response = await c.post(
            "/api/v1/auth/signup/start",
            headers=ORIGIN,
            json={
                "accountType": "individual",
                "displayName": "Synthetic",
                "email": "proof@example.com",
            },
        )
        cid = response.json()["data"]["challengeId"]
        code = app.state.signup_delivery.messages["proof@example.com"]
        response = await c.post(
            "/api/v1/auth/signup/verify", headers=ORIGIN, json={"challengeId": cid, "code": code}
        )
        setup = response.json()["data"]["setupToken"]
        original = signup_service.record

        async def interrupt(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("Synthetic audit interruption")

        monkeypatch.setattr(signup_service, "record", interrupt)
        payload = {"challengeId": cid, "setupToken": setup, "password": "synthetic-long-password"}
        with pytest.raises(RuntimeError, match="Synthetic audit"):
            await c.post("/api/v1/auth/signup/complete", headers=ORIGIN, json=payload)
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 0
            assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        monkeypatch.setattr(signup_service, "record", original)
        assert (
            await c.post("/api/v1/auth/signup/complete", headers=ORIGIN, json=payload)
        ).status_code == 201
        data = await events(c)
        assert [event["action"] for event in data["events"]] == ["auth.signup.completed"]
        assert code not in str(data) and setup not in str(data)
        assert (
            await c.post("/api/v1/auth/signup/complete", headers=ORIGIN, json=payload)
        ).status_code == 400
        assert len((await events(c))["events"]) == 1


@pytest.mark.skipif(
    not __import__("os").getenv("DOCUMENT_TEST_DATABASE_URL"), reason="PostgreSQL locks required"
)
async def test_concurrent_intake_completion_has_one_event(app):
    import asyncio

    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        attempt = await start(c, doc)
        assert (await put(c, attempt)).status_code == 204
        responses = await asyncio.gather(finish(c, doc, attempt), finish(c, doc, attempt))
        assert [response.status_code for response in responses] == [200, 200]
        assert sum(response.json()["data"]["replayed"] for response in responses) == 1
        assert len((await events(c, action="document.intake.completed"))["events"]) == 1


async def test_failure_recording_preserves_safe_response_when_actor_disappears(app, monkeypatch):
    import app.main as main

    async with client(app) as c:
        await signup(c)

        async def gone(*args, **kwargs):
            raise ValueError("Audit actor must belong to the recorded tenant")

        monkeypatch.setattr(main, "record", gone)
        response = await c.get(f"/api/v1/documents/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Document was not found."
        assert "actor" not in response.text


async def test_structurally_invalid_cursor_is_safe_bad_request(app):
    import base64
    import json

    async with client(app) as c:
        identity = await signup(c)
        scope = [identity["organizationId"], "audit-v1", None, None]
        for invalid in [123, {}, [], None, True]:
            cursor = base64.urlsafe_b64encode(
                json.dumps([scope, "2026-01-01T00:00:00+00:00", invalid]).encode()
            ).decode()
            response = await c.get("/api/v1/audit/events", params={"cursor": cursor})
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "INVALID_CURSOR"
