import asyncio
import hashlib
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.core.config import Settings
from app.db.base import Base
from app.db.models import User
from app.domain.documents.models import Document, Upload
from app.domain.documents.routes import now
from app.main import create_app

ORIGIN = {"Origin": "http://localhost:3000"}
PDF = b"%PDF-1.4\nSynthetic intake fixture only.\n%%EOF"


@pytest_asyncio.fixture
async def app(tmp_path):
    app = create_app(
        Settings(
            database_url=os.environ.get(
                "DOCUMENT_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/docs.db"
            ),
            document_storage_root=str(tmp_path / "private"),
        )
    )
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield app
    await app.state.engine.dispose()


def client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:3000")


async def signup(c, email="one@example.com"):
    result = await c.post(
        "/api/v1/auth/register",
        headers=ORIGIN,
        json={
            "displayName": "Synthetic User",
            "email": email,
            "password": "synthetic-long-password",
            "organizationName": "Synthetic Org",
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["data"]["user"]


def metadata(content=PDF, **overrides):
    return {
        "originalFileName": "fixture.pdf",
        "mediaType": "application/pdf",
        "sizeBytes": len(content),
        "checksumSha256": hashlib.sha256(content).hexdigest(),
        **overrides,
    }


async def create(c, content=PDF, **overrides):
    r = await c.post("/api/v1/documents", headers=ORIGIN, json=metadata(content, **overrides))
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def start(c, doc, key="one"):
    r = await c.post(
        f"/api/v1/documents/{doc['id']}/uploads", headers={**ORIGIN, "Idempotency-Key": key}
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def put(c, data, content=PDF):
    return await c.put(
        data["target"]["uploadUrl"],
        headers={**ORIGIN, **data["target"]["requiredHeaders"]},
        content=content,
    )


async def finish(c, doc, upload):
    return await c.post(
        f"/api/v1/documents/{doc['id']}/uploads/{upload['attempt']['id']}/complete", headers=ORIGIN
    )


async def test_actual_bytes_remain_private_pending_scan_and_immutable(app):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        assert doc["scanStatus"] == "awaiting_upload"
        upload = await start(c, doc)
        assert (await finish(c, doc, upload)).status_code == 409
        assert (await put(c, upload)).status_code == 204
        files = list(app.state.quarantine_storage.root.glob("*.quarantine"))
        assert len(files) == 1 and files[0].read_bytes() == PDF
        assert files[0].stat().st_mode & 0o777 == 0o600
        assert "fixture.pdf" not in files[0].name
        result = await finish(c, doc, upload)
        assert result.status_code == 200, result.text
        assert result.json()["data"]["attempt"]["status"] == "completed"
        assert (await finish(c, doc, upload)).json()["data"]["replayed"] is True
        assert (await put(c, upload)).status_code == 409
        read = await c.get(f"/api/v1/documents/{doc['id']}")
        assert read.json()["data"]["scanStatus"] == "pending_scan"
        assert read.json()["data"]["status"] == "received"
        assert "no-store" in read.headers["cache-control"]
        assert str(app.state.quarantine_storage.root) not in read.text
        assert (await c.get(upload["target"]["uploadUrl"])).status_code == 405
        blocked = await c.post(
            f"/api/v1/documents/{doc['id']}/extractions",
            headers={**ORIGIN, "Idempotency-Key": "extract"},
            json={"schemaVersion": "invoice-v1"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "DOCUMENT_NOT_READY"


async def test_cross_tenant_every_document_and_upload_access(app):
    async with client(app) as a, client(app) as b:
        await signup(a)
        await signup(b, "two@example.com")
        doc = await create(a)
        upload = await start(a, doc)
        assert (await put(b, upload)).status_code == 404
        assert (await finish(b, doc, upload)).status_code == 404
        for path in [
            f"/api/v1/documents/{doc['id']}",
            f"/api/v1/documents/{doc['id']}/extractions",
        ]:
            r = await b.get(path)
            assert r.status_code == 404
            assert doc["organizationId"] not in r.text
        assert (await b.get("/api/v1/documents")).json()["data"]["items"] == []
        assert (
            await b.post(
                f"/api/v1/documents/{doc['id']}/uploads", headers={**ORIGIN, "Idempotency-Key": "x"}
            )
        ).status_code == 404
        assert (await create(b))["organizationId"] != doc["organizationId"]


@pytest.mark.parametrize(
    "role,read,write",
    [
        ("viewer", 200, 403),
        ("auditor", 403, 403),
        ("operator", 200, 201),
        ("manager", 200, 201),
        ("platform_admin", 200, 201),
    ],
)
async def test_live_database_roles_enforced(app, role, read, write):
    async with client(app) as c:
        await signup(c)
        async with app.state.session_factory() as db:
            await db.execute(update(User).values(role=role))
            await db.commit()
        assert (await c.get("/api/v1/documents")).status_code == read
        assert (
            await c.post("/api/v1/documents", headers=ORIGIN, json=metadata())
        ).status_code == write


@pytest.mark.parametrize(
    "overrides",
    [
        {"originalFileName": "../escape.pdf"},
        {"originalFileName": "C:\\secret.pdf"},
        {"originalFileName": "a\x00.pdf"},
        {"originalFileName": "a\u202e.pdf"},
        {"mediaType": "text/html"},
        {"sizeBytes": 0},
        {"sizeBytes": 26214401},
        {"sizeBytes": True},
        {"checksumSha256": "not-a-hash"},
        {"organizationId": str(uuid4())},
        {"status": "completed"},
        {"scanStatus": "clean"},
    ],
)
async def test_reject_invalid_untrusted_metadata(app, overrides):
    async with client(app) as c:
        await signup(c)
        response = await c.post("/api/v1/documents", headers=ORIGIN, json=metadata(**overrides))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_csrf_auth_duplicate_and_body_bounds(app):
    async with client(app) as c:
        assert (await c.get("/api/v1/documents")).status_code == 401
        await signup(c)
        assert (await c.post("/api/v1/documents", json=metadata())).status_code == 403
        await create(c)
        r = await c.post("/api/v1/documents", headers=ORIGIN, json=metadata())
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "DOCUMENT_CHECKSUM_CONFLICT"
        r = await c.post("/api/v1/documents", headers=ORIGIN, content=b" " * 32769)
        assert r.status_code == 413
        assert r.json()["error"]["requestId"] == r.json()["meta"]["requestId"]


@pytest.mark.parametrize(
    "content,status", [(PDF + b"X", 413), (PDF[:-1], 409), (b"x" * len(PDF), 409)]
)
async def test_upload_integrity_size_failure_cleans_partial_and_retry(app, content, status):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        assert (await put(c, upload, content)).status_code == status
        assert list(app.state.quarantine_storage.root.iterdir()) == []
        assert (await finish(c, doc, upload)).status_code == 409
        assert (await put(c, upload)).status_code == 204


async def test_claimed_media_signature_must_match(app):
    content = b"<script>synthetic</script>"
    async with client(app) as c:
        await signup(c)
        doc = await create(c, content)
        upload = await start(c, doc)
        assert (await put(c, upload, content)).status_code == 415
        assert list(app.state.quarantine_storage.root.iterdir()) == []


async def test_idempotency_attempt_expiry_and_document_mismatch(app):
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        replay = await start(c, doc)
        assert replay["replayed"] is True and replay["attempt"]["id"] == upload["attempt"]["id"]
        other = await create(c, PDF + b"2")
        r = await c.post(
            f"/api/v1/documents/{other['id']}/uploads", headers={**ORIGIN, "Idempotency-Key": "one"}
        )
        assert r.status_code == 409
        async with app.state.session_factory() as db:
            await db.execute(update(Upload).values(expires_at=now() - timedelta(seconds=1)))
            await db.commit()
        assert (await put(c, upload)).status_code == 409
        upload = await start(c, doc)
        assert (await put(c, upload)).status_code == 204
        r = await c.post(
            f"/api/v1/documents/{other['id']}/uploads/{upload['attempt']['id']}/complete",
            headers=ORIGIN,
        )
        assert r.status_code == 404


async def test_bounded_pagination_no_duplicate_and_scoped_cursor(app):
    async with client(app) as a, client(app) as b:
        await signup(a)
        await signup(b, "two@example.com")
        ids = {(await create(a, PDF + str(i).encode()))["id"] for i in range(3)}
        first = (await a.get("/api/v1/documents?limit=2")).json()["data"]
        assert len(first["items"]) == 2 and first["nextCursor"]
        second = (
            await a.get("/api/v1/documents", params={"limit": 2, "cursor": first["nextCursor"]})
        ).json()["data"]
        assert {row["id"] for row in first["items"] + second["items"]} == ids
        assert second["nextCursor"] is None
        assert (
            await b.get("/api/v1/documents", params={"cursor": first["nextCursor"]})
        ).status_code == 400
        for params in [{"limit": 101}, {"limit": 0}, {"cursor": "bad"}, {"tenantId": str(uuid4())}]:
            assert (await a.get("/api/v1/documents", params=params)).status_code == 400


async def test_disabled_storage_and_extractor_are_honest(app):
    app.state.quarantine_storage = None
    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        r = await c.post(
            f"/api/v1/documents/{doc['id']}/uploads", headers={**ORIGIN, "Idempotency-Key": "x"}
        )
        assert r.status_code == 503 and r.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
        async with app.state.session_factory() as db:
            assert list(await db.scalars(select(Upload))) == []
            await db.execute(update(Document).values(scan_status="clean"))
            await db.commit()
        r = await c.post(
            f"/api/v1/documents/{doc['id']}/extractions",
            headers={**ORIGIN, "Idempotency-Key": "x"},
            json={"schemaVersion": "invoice-v1"},
        )
        assert r.status_code == 503 and r.json()["error"]["code"] == "EXTRACTOR_UNAVAILABLE"


async def test_concurrent_duplicate_metadata(app):
    async with client(app) as c:
        await signup(c)
        results = await asyncio.gather(
            *(c.post("/api/v1/documents", headers=ORIGIN, json=metadata()) for _ in range(2))
        )
        assert sorted(r.status_code for r in results) == [201, 409]


def test_openapi_documents_contracts(app):
    schema = app.openapi()
    assert "CreateDocument" in schema["components"]["schemas"]
    assert schema["components"]["schemas"]["CreateDocument"]["additionalProperties"] is False
    assert "scanStatus" in schema["components"]["schemas"]["DocumentRecord"]["properties"]
    for path, routes in schema["paths"].items():
        if "/documents" in path:
            for method, route in routes.items():
                assert "401" in route["responses"] and "503" in route["responses"]
    assert "put" in schema["paths"]["/api/v1/documents/{document_id}/uploads/{attempt_id}/content"]


async def test_upload_admission_and_deadline_release_capacity(app):
    import anyio

    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        app.state.upload_slots = anyio.CapacityLimiter(1)
        app.state.upload_slots.acquire_nowait()
        try:
            response = await put(c, upload)
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "UPLOAD_CAPACITY"
        finally:
            app.state.upload_slots.release()
        app.state.upload_timeout_seconds = 0.02

        async def slow():
            yield PDF[:5]
            await asyncio.sleep(0.2)
            yield PDF[5:]

        response = await c.put(
            upload["target"]["uploadUrl"],
            headers={**ORIGIN, **upload["target"]["requiredHeaders"]},
            content=slow(),
        )
        assert response.status_code == 408, response.text
        assert app.state.upload_slots.borrowed_tokens == 0
        assert list(app.state.quarantine_storage.root.iterdir()) == []
        app.state.upload_timeout_seconds = 65
        assert (await put(c, upload)).status_code == 204


async def test_stale_partial_recovery_and_disk_reserve(app, monkeypatch):
    import time
    from collections import namedtuple
    from uuid import UUID

    async with client(app) as c:
        await signup(c)
        doc = await create(c)
        upload = await start(c, doc)
        path = app.state.quarantine_storage.path(
            UUID(doc["organizationId"]), UUID(doc["id"]), UUID(upload["attempt"]["id"])
        )
        partial = path.with_suffix(".partial")
        partial.write_bytes(b"crashed synthetic partial")
        old = time.time() - 200
        os.utime(partial, (old, old))
        assert (await put(c, upload)).status_code == 204
        assert not partial.exists()
        other = await create(c, PDF + b"2")
        target = await start(c, other, "other")
        usage = namedtuple("usage", "total used free")
        monkeypatch.setattr(
            "app.domain.documents.storage.shutil.disk_usage", lambda _: usage(100, 99, 1)
        )
        response = await put(c, target, PDF + b"2")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "STORAGE_CAPACITY"


async def test_upload_live_role_revocation_and_no_platform_tenant_bypass(app):
    async with client(app) as a, client(app) as b:
        first = await signup(a)
        await signup(b, "two@example.com")
        doc = await create(a)
        upload = await start(a, doc)
        async with app.state.session_factory() as db:
            await db.execute(update(User).where(User.email == first["email"]).values(role="viewer"))
            await db.execute(
                update(User).where(User.email != first["email"]).values(role="platform_admin")
            )
            await db.commit()
        assert (await put(a, upload)).status_code == 403
        assert (await finish(a, doc, upload)).status_code == 403
        assert (await put(b, upload)).status_code == 404
        assert (await finish(b, doc, upload)).status_code == 404
        assert (await c_get(b, doc)).status_code == 404


async def c_get(c, doc):
    return await c.get(f"/api/v1/documents/{doc['id']}")
