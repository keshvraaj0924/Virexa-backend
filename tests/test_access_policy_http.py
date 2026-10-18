# ruff: noqa: F811
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from test_document_intake import (  # noqa: F401
    ORIGIN,
    app,
    client,
    create,
    finish,
    metadata,
    put,
    signup,
    start,
)

from app.core.rbac import Role
from app.db.models import Session, User
from app.domain.documents.models import Document, Upload
from app.domain.documents.routes import now

BASE = "/api/v1"


@pytest.mark.parametrize(
    "role,expected",
    [
        (
            "platform_admin",
            [
                "document:create",
                "document:read",
                "document:upload",
                "organization:read",
                "session:read",
                "session:revoke",
            ],
        ),
        (
            "tenant_admin",
            [
                "document:create",
                "document:read",
                "document:upload",
                "organization:read",
                "session:read",
                "session:revoke",
            ],
        ),
        (
            "organization_admin",
            [
                "document:create",
                "document:read",
                "document:upload",
                "organization:read",
                "session:read",
                "session:revoke",
            ],
        ),
        (
            "manager",
            [
                "document:create",
                "document:read",
                "document:upload",
                "organization:read",
                "session:read",
                "session:revoke",
            ],
        ),
        (
            "operator",
            [
                "document:create",
                "document:read",
                "document:upload",
                "session:read",
                "session:revoke",
            ],
        ),
        ("viewer", ["document:read", "session:read", "session:revoke"]),
        ("auditor", ["session:read", "session:revoke"]),
    ],
)
async def test_effective_capabilities_use_live_database_role(app, role, expected):
    if role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN, Role.MANAGER}:
        expected += ["document:layout:request"]
    if role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN, Role.AUDITOR}:
        expected += ["organization:members:read", "organization:hierarchy:read"]
    if role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN}:
        expected += ["organization:members:manage"]
    expected = sorted(
        expected
        + ["usage:read"]
        + (["workflow:read"] if role != Role.AUDITOR else [])
        + (["workflow:create"] if role not in {Role.AUDITOR, Role.VIEWER} else [])
        + (
            ["workflow:manage"]
            if role
            in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN, Role.MANAGER}
            else []
        )
        + (
            ["usage:organization:read", "audit:read"]
            if role
            in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN, Role.AUDITOR}
            else []
        )
    )
    async with client(app) as c:
        assert (await c.get(BASE + "/me/permissions")).status_code == 401
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        async with app.state.session_factory() as db:
            await db.execute(update(User).values(role=role))
            await db.commit()
        headers = {"X-Role": "platform_admin", "X-Organization-ID": str(uuid4())}
        response = await c.get(BASE + "/me/permissions", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"] == {"capabilities": expected}
        assert "no-store" in response.headers["cache-control"]
        assert (await c.get(BASE + "/me")).status_code == 200
        assert (await c.get(BASE + "/auth/sessions")).status_code == 200
        assert (await c.get(BASE + "/documents")).status_code == (
            200 if "document:read" in expected else 403
        )
        write = await put(c, upload)
        assert write.status_code == (204 if "document:upload" in expected else 403)
        response = await c.post(
            BASE + "/documents", headers={**ORIGIN, **headers}, json=metadata(b"%PDF-1.4 other")
        )
        assert response.status_code == (201 if "document:create" in expected else 403)
        async with app.state.session_factory() as db:
            await db.execute(update(User).values(role="invalid"))
            await db.commit()
        assert (await c.get(BASE + "/me/permissions")).status_code == 403
        assert (await c.get(BASE + "/auth/sessions")).status_code == 403


@pytest.mark.parametrize(
    "status,scan",
    [
        ("processing", "awaiting_upload"),
        ("completed", "awaiting_upload"),
        ("failed", "awaiting_upload"),
        ("received", "rejected"),
        ("received", "pending_scan"),
        ("received", "clean"),
    ],
)
async def test_changed_resource_state_blocks_upload_and_completion(app, status, scan):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        assert (await put(c, upload)).status_code == 204
        async with app.state.session_factory() as db:
            await db.execute(update(Document).values(status=status, scan_status=scan))
            await db.commit()
        content = await put(c, upload)
        complete = await finish(c, doc, upload)
        initiate = await c.post(
            f"{BASE}/documents/{doc['id']}/uploads", headers={**ORIGIN, "Idempotency-Key": "one"}
        )
        for response in (content, complete, initiate):
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "DOCUMENT_NOT_WRITABLE"
        async with app.state.session_factory() as db:
            row = await db.get(Upload, UUID(upload["attempt"]["id"]))
            assert row.status == "initiated"


async def test_expired_completion_denied_until_original_attempt_renewed(app):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        assert (await put(c, upload)).status_code == 204
        async with app.state.session_factory() as db:
            await db.execute(update(Upload).values(expires_at=now() - timedelta(seconds=1)))
            await db.commit()
        assert (await finish(c, doc, upload)).json()["error"]["code"] == "UPLOAD_NOT_WRITABLE"
        renewed = await start(c, doc)
        assert renewed["attempt"]["id"] == upload["attempt"]["id"]
        assert (await finish(c, doc, renewed)).status_code == 200


async def test_capabilities_do_not_grant_foreign_sessions_or_documents(app):
    async with client(app) as a, client(app) as b:
        owner = await signup(a)
        other = await signup(b, "other@example.com")
        doc = await create(b)
        foreign_session = (await b.get(BASE + "/auth/sessions")).json()["data"][0]["id"]
        assert (await a.get(BASE + "/me/permissions")).status_code == 200
        for identifier in (doc["id"], str(uuid4())):
            response = await a.get(BASE + "/documents/" + identifier)
            assert response.status_code == 404
            assert response.json()["error"]["message"] == "Document was not found."
        # Even platform_admin cannot revoke another owner's session in the same tenant.
        async with app.state.session_factory() as db:
            await db.execute(
                update(User).where(User.id == UUID(owner["id"])).values(role="platform_admin")
            )
            await db.execute(
                update(User)
                .where(User.id == UUID(other["id"]))
                .values(organization_id=UUID(owner["organizationId"]))
            )
            await db.execute(
                update(Session)
                .where(Session.user_id == UUID(other["id"]))
                .values(organization_id=UUID(owner["organizationId"]))
            )
            await db.commit()
        for identifier in (foreign_session, str(uuid4())):
            response = await a.delete(BASE + "/auth/sessions/" + identifier, headers=ORIGIN)
            assert response.status_code == 404
            assert response.json()["error"]["message"] == "Active session was not found."
        assert (await b.get(BASE + "/me/permissions")).status_code == 200


async def test_completed_replay_does_not_reset_rejected_scan_or_unlock_bytes(app):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        assert (await put(c, upload)).status_code == 204
        assert (await finish(c, doc, upload)).status_code == 200
        async with app.state.session_factory() as db:
            await db.execute(update(Document).values(scan_status="rejected", status="failed"))
            await db.commit()
        replay = await finish(c, doc, upload)
        assert replay.status_code == 200 and replay.json()["data"]["replayed"] is True
        assert (await start(c, doc))["replayed"] is True
        assert (await put(c, upload)).status_code == 409
        async with app.state.session_factory() as db:
            row = await db.get(Document, UUID(doc["id"]))
            assert (row.scan_status, row.status) == ("rejected", "failed")


async def test_first_attempt_on_non_intake_document_is_denied_without_creation(app):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        async with app.state.session_factory() as db:
            await db.execute(update(Document).values(scan_status="rejected"))
            await db.commit()
        response = await c.post(
            f"{BASE}/documents/{doc['id']}/uploads",
            headers={**ORIGIN, "Idempotency-Key": "new"},
        )
        assert response.status_code == 409
        async with app.state.session_factory() as db:
            from sqlalchemy import select

            assert (await db.scalars(select(Upload))).all() == []


async def test_failed_verified_attempt_cannot_renew_write_or_complete(app):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        assert (await put(c, upload)).status_code == 204
        async with app.state.session_factory() as db:
            await db.execute(
                update(Upload).values(status="failed", expires_at=now() - timedelta(seconds=1))
            )
            await db.commit()
        renewal = await c.post(
            f"{BASE}/documents/{doc['id']}/uploads",
            headers={**ORIGIN, "Idempotency-Key": "one"},
        )
        for response in (renewal, await put(c, upload), await finish(c, doc, upload)):
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "UPLOAD_NOT_WRITABLE"
        async with app.state.session_factory() as db:
            row = await db.get(Upload, UUID(upload["attempt"]["id"]))
            document = await db.get(Document, UUID(doc["id"]))
            assert row.status == "failed" and row.completed_at is None
            assert document.scan_status == "awaiting_upload"
