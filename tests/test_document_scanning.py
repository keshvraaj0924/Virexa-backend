"""Synthetic bytes and local fake protocol; genuine-engine test is opt-in below."""

# ruff: noqa: F811
import asyncio
import os
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, update
from test_document_intake import (  # noqa: F401
    ORIGIN,
    PDF,
    app,
    client,
    create,
    finish,
    put,
    signup,
    start,
)

from app.domain.audit.models import AuditEvent
from app.domain.documents.models import Document, ScanJob
from app.domain.documents.scan_jobs import claim, now, run_once
from app.domain.documents.scanner import ClamAVScanner


async def intake(app, email="scan@example.com", content=PDF):
    async with client(app) as c:
        await signup(c, email)
        doc = await create(c, content)
        upload = await start(c, doc)
        assert (await put(c, upload, content)).status_code == 204
        assert (await finish(c, doc, upload)).status_code == 200
        assert (await finish(c, doc, upload)).status_code == 200
    return doc


@pytest.fixture
async def daemon(tmp_path, monkeypatch):
    state = {"reply": b"stream: OK", "date": now(), "bodies": [], "delay": 0}

    async def serve(reader, writer):
        try:
            command = await reader.readuntil(b"\0")
            if command == b"zVERSION\0":
                reply = (
                    "ClamAV 1.4.3/12345/" + state["date"].strftime("%a %b %d %H:%M:%S %Y")
                ).encode()
            else:
                assert command == b"zINSTREAM\0"
                content = b""
                while True:
                    length = int.from_bytes(await reader.readexactly(4), "big")
                    if not length:
                        break
                    content += await reader.readexactly(length)
                state["bodies"].append(content)
                await asyncio.sleep(state["delay"])
                reply = state["reply"]
            writer.write(reply + b"\0")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    path = str(tmp_path / "clam.sock")
    # CI exercises Unix sockets. This sandbox permits local TCP but denies AF_UNIX.
    if os.environ.get("SCAN_TEST_TCP_TRANSPORT") == "1":
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def local_transport(_path, **kwargs):
            return await asyncio.open_connection("127.0.0.1", port, **kwargs)

        monkeypatch.setattr(asyncio, "open_unix_connection", local_transport)
        # Socket trust itself is tested separately; no production transport fallback.
        monkeypatch.setattr(ClamAVScanner, "_trusted_socket", lambda self: None)
    else:
        server = await asyncio.start_unix_server(serve, path)
    yield path, state
    server.close()
    await server.wait_closed()


@pytest.mark.parametrize(
    "reply,verdict",
    [
        (b"stream: OK", "clean"),
        (b"stream: Eicar-Test FOUND", "rejected"),
        (b"stream: size limit ERROR", "error"),
        (b"OK", "error"),
        (b"stream: OK\0stream: hidden ERROR", "error"),
    ],
)
async def test_protocol_strict_replies(daemon, reply, verdict):
    path, state = daemon
    state["reply"] = reply
    result = await ClamAVScanner(path).scan(PDF)
    assert result.verdict == verdict
    assert state["bodies"] == [PDF]
    assert "Eicar" not in result.code


async def test_stale_signatures_fail_closed(daemon):
    path, state = daemon
    state["date"] = now() - timedelta(days=3)
    assert (await ClamAVScanner(path).scan(PDF)).code == "signatures_stale"
    assert state["bodies"] == []


async def test_scanner_unavailable_and_bounds(tmp_path):
    scanner = ClamAVScanner(str(tmp_path / "absent"))
    assert (await scanner.scan(PDF)).verdict == "error"
    assert (await scanner.scan(b"")).code == "size_limit"
    with pytest.raises(ValueError):
        ClamAVScanner("https://vendor.example")


@pytest.mark.parametrize(
    "reply,status", [(b"stream: OK", "clean"), (b"stream: Eicar-Test FOUND", "rejected")]
)
async def test_worker_real_protocol_persists_verdict_no_extraction(app, daemon, reply, status):
    doc = await intake(app)
    path, state = daemon
    state["reply"] = reply
    assert await run_once(app.state.session_factory, app.state.quarantine_storage, path)
    async with app.state.session_factory() as db:
        row = await db.get(Document, UUID(doc["id"]))
        jobs = list(await db.scalars(select(ScanJob)))
        assert row.scan_status == status and row.status == "received"
        assert len(jobs) == 1 and jobs[0].state == status and jobs[0].attempts == 1
        events = list(
            await db.scalars(
                select(AuditEvent).where(AuditEvent.action == f"document.scan.{status}")
            )
        )
        assert len(events) == 1
        assert events[0].details == {"outcome": "succeeded" if status == "clean" else "failed"}
    assert not await run_once(app.state.session_factory, app.state.quarantine_storage, path)
    assert list(app.state.quarantine_storage.root.glob("*.quarantine"))[0].read_bytes() == PDF
    async with client(app) as c:
        await c.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "scan@example.com", "password": "synthetic-long-password"},
        )
        result = await c.post(
            f"/api/v1/documents/{doc['id']}/extractions",
            headers={**ORIGIN, "Idempotency-Key": "no-ocr"},
            json={"schemaVersion": "invoice-v1"},
        )
        assert result.status_code in (409, 503)
        assert (
            await c.post(f"/api/v1/documents/{doc['id']}/scan", json={"scanStatus": "clean"})
        ).status_code in (404, 405)


async def test_disabled_and_retry_budget(app, tmp_path):
    await intake(app)
    factory, storage = app.state.session_factory, app.state.quarantine_storage
    assert not await run_once(factory, storage, None)
    for attempt in range(1, 4):
        assert await run_once(factory, storage, str(tmp_path / "absent"))
        async with factory() as db:
            job = await db.scalar(select(ScanJob))
            assert job.attempts == attempt
            assert job.state == ("error" if attempt == 3 else "pending")
            assert (await db.get(Document, job.document_id)).scan_status == "pending_scan"
            # No hot-loop retry; advancing the synthetic clock makes it eligible.
            if attempt < 3:
                assert not await run_once(factory, storage, str(tmp_path / "absent"))
                job.available_at = now() - timedelta(seconds=1)
                await db.commit()
    assert not await run_once(factory, storage, str(tmp_path / "absent"))


async def test_hash_mismatch_never_reaches_scanner(app, daemon):
    await intake(app)
    path, state = daemon
    file = list(app.state.quarantine_storage.root.glob("*.quarantine"))[0]
    file.write_bytes(b"X" * len(PDF))
    await run_once(app.state.session_factory, app.state.quarantine_storage, path)
    assert state["bodies"] == []
    async with app.state.session_factory() as db:
        assert (await db.scalar(select(Document))).scan_status == "pending_scan"
        assert (await db.scalar(select(ScanJob))).result_code == "integrity_failed"


async def test_expired_worker_cannot_complete(app, daemon, monkeypatch):
    await intake(app)
    path, state = daemon
    original = ClamAVScanner.scan

    async def expire(self, data):
        result = await original(self, data)
        async with app.state.session_factory() as db:
            await db.execute(update(ScanJob).values(lease_until=now() - timedelta(seconds=1)))
            await db.commit()
        return result

    monkeypatch.setattr(ClamAVScanner, "scan", expire)
    await run_once(app.state.session_factory, app.state.quarantine_storage, path)
    async with app.state.session_factory() as db:
        assert (await db.scalar(select(Document))).scan_status == "pending_scan"
        first = await db.scalar(select(ScanJob))
        old_token = first.lease_token
        claimed = await claim(db)
        assert claimed[1] != old_token


async def test_postgres_concurrent_claims_and_global_admission(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL lock semantics")
    for index in range(6):
        await intake(app, f"scan{index}@example.com")

    async def take():
        async with app.state.session_factory() as db:
            return await claim(db)

    claims = await asyncio.gather(*(take() for _ in range(12)))
    claimed = [item for item in claims if item]
    assert len(claimed) == 4
    assert len({item[0] for item in claimed}) == 4


async def test_genuine_clamav_engine():
    path = os.environ.get("CLAMAV_TEST_SOCKET")
    if not path:
        pytest.skip("Real ClamAV daemon not configured; protocol tests are not engine evidence")
    scanner = ClamAVScanner(path)
    assert (await scanner.scan(PDF)).verdict == "clean"
    # Standard harmless antivirus test string, never executable malware.
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    assert (await scanner.scan(eicar)).verdict == "rejected"


async def test_one_tenant_slot_and_exhausted_crash_lease(app):
    async with client(app) as c:
        await signup(c, "slots@example.com")
        for index in range(2):
            content = PDF + str(index).encode()
            doc = await create(c, content)
            upload = await start(c, doc, key=f"upload-{index}")
            assert (await put(c, upload, content)).status_code == 204
            assert (await finish(c, doc, upload)).status_code == 200
    async with app.state.session_factory() as db:
        first = await claim(db)
        assert first
        assert await claim(db) is None
        job = await db.get(ScanJob, first[0])
        job.attempts = 3
        job.lease_until = now() - timedelta(seconds=1)
        await db.commit()
        assert await claim(db)
        await db.refresh(job)
        assert job.state == "error" and job.result_code == "lease_exhausted"
        assert (await db.get(Document, job.document_id)).scan_status == "pending_scan"


async def test_cross_tenant_job_binding_and_public_status(app):
    from sqlalchemy.exc import IntegrityError

    first = await intake(app, "firstscan@example.com")
    second = await intake(app, "secondscan@example.com")
    async with app.state.session_factory() as db:
        job = await db.scalar(select(ScanJob).where(ScanJob.document_id == UUID(first["id"])))
        job.organization_id = UUID(second["organizationId"])
        if app.state.engine.dialect.name == "postgresql":
            with pytest.raises(IntegrityError):
                await db.commit()
        else:
            await db.rollback()  # SQLite fixture does not enforce foreign keys.
    async with client(app) as c:
        response = await c.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "secondscan@example.com", "password": "synthetic-long-password"},
        )
        assert response.status_code == 200
        response = await c.get(f"/api/v1/documents/{first['id']}")
        assert response.status_code == 404
        response = await c.get(f"/api/v1/documents/{second['id']}")
        assert response.json()["data"]["scanStatus"] == "pending_scan"


async def test_mutation_during_scan_cannot_be_clean(app, daemon, monkeypatch):
    await intake(app)
    path, _ = daemon
    original = ClamAVScanner.scan

    async def mutate(self, data):
        result = await original(self, data)
        file = list(app.state.quarantine_storage.root.glob("*.quarantine"))[0]
        file.write_bytes(b"Z" * len(PDF))
        return result

    monkeypatch.setattr(ClamAVScanner, "scan", mutate)
    await run_once(app.state.session_factory, app.state.quarantine_storage, path)
    async with app.state.session_factory() as db:
        assert (await db.scalar(select(Document))).scan_status == "pending_scan"
        assert (await db.scalar(select(ScanJob))).result_code == "integrity_failed"


def test_socket_trust_rejects_regular_files_symlinks_and_writable_parents(tmp_path):
    import stat
    from unittest.mock import patch

    path = tmp_path / "not-a-socket"
    path.write_text("synthetic")
    with pytest.raises(ValueError):
        ClamAVScanner(str(path))._trusted_socket()
    link = tmp_path / "symlink"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        ClamAVScanner(str(link))._trusted_socket()
    # Socket permission/ownership checks without binding an AF_UNIX socket.
    from types import SimpleNamespace

    for mode, owner in [
        (stat.S_IFSOCK | 0o666, os.getuid()),
        (stat.S_IFSOCK | 0o600, os.getuid() + 1),
    ]:
        with patch("pathlib.Path.lstat", return_value=SimpleNamespace(st_mode=mode, st_uid=owner)):
            with pytest.raises(ValueError):
                ClamAVScanner(str(path))._trusted_socket()


async def test_protocol_total_timeout(daemon):
    path, state = daemon
    state["delay"] = 1.1
    result = await ClamAVScanner(path, timeout=1).scan(PDF)
    assert result.verdict == "error" and result.code == "scanner_timeout"
    await asyncio.sleep(0.15)
