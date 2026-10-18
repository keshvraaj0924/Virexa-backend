"""Synthetic intake and explicitly mocked parser test orchestration, not Docling accuracy."""
# ruff: noqa: F811
import asyncio
import base64
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

from app.db.models import User
from app.domain.audit.models import AuditEvent
from app.domain.documents.layout_jobs import claim, run_once
from app.domain.documents.layout_models import LayoutJob
from app.domain.documents.models import Document, ScanJob
from app.domain.documents.scan_jobs import now

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC')


class MockParser:
    """Never represents real parser or OCR evidence."""
    calls = 0

    async def parse(self, data):
        from types import SimpleNamespace
        self.calls += 1
        return SimpleNamespace(canonical={"pages": {"1": {"page_no": 1}},
                                         "texts": [{"text": "Synthetic fixture"}]},
                               page_count=1, parser_version="mock", model_version="mock",
                               warnings=("synthetic_mock",), previews=(PNG,))


async def prepare(app, c, email="layout@example.com", clean=True):
    await signup(c, email)
    doc = await create(c)
    upload = await start(c, doc)
    assert (await put(c, upload)).status_code == 204
    assert (await finish(c, doc, upload)).status_code == 200
    if clean:
        # Unit setup only; genuine scanner evidence belongs to test_document_scanning.
        async with app.state.session_factory() as db:
            row = await db.get(Document, UUID(doc["id"]))
            row.scan_status = "clean"
            job = await db.scalar(select(ScanJob).where(ScanJob.document_id == row.id))
            job.state = "clean"
            await db.commit()
    return doc


def enable(app):
    app.state.settings.document_layout_enabled = True
    app.state.settings.document_layout_assets = "/synthetic/mock/assets"


async def request(c, doc):
    return await c.post(f"/api/v1/documents/{doc['id']}/layout-jobs", headers=ORIGIN, json={})


async def test_disabled_scan_gating_and_no_result_mutation(app):
    async with client(app) as c:
        doc = await prepare(app, c, clean=False)
        enable(app)
        assert (await request(c, doc)).status_code == 409
        async with app.state.session_factory() as db:
            assert not list(await db.scalars(select(LayoutJob)))
            row = await db.get(Document, UUID(doc["id"]))
            row.scan_status = "clean"
            await db.commit()
        # A forged clean status without matching scan record is insufficient.
        assert (await request(c, doc)).status_code == 409
        app.state.settings.document_layout_enabled = False
        assert (await request(c, doc)).status_code == 503
        assert (await c.post(f"/api/v1/documents/{doc['id']}/layout-jobs/complete",
                             headers=ORIGIN, json={"state": "needs_review"})).status_code in (404, 405)


async def test_mock_result_is_review_required_with_private_source_preview(app):
    enable(app)
    async with client(app) as c:
        doc = await prepare(app, c)
        first = await request(c, doc)
        assert first.status_code == 202, first.text
        job = first.json()["data"]["job"]
        assert first.json()["data"]["replayed"] is False
        assert (await request(c, doc)).json()["data"]["replayed"] is True
        base = f"/api/v1/documents/{doc['id']}/layout-jobs/{job['id']}"
        assert (await c.get(base + "/result")).status_code == 409
        assert not await run_once(app.state.session_factory, app.state.quarantine_storage, None)
        parser = MockParser()
        assert await run_once(app.state.session_factory, app.state.quarantine_storage, parser)
        assert not await run_once(app.state.session_factory, app.state.quarantine_storage, parser)
        assert parser.calls == 1
        status = (await c.get(base)).json()["data"]
        assert status["state"] == "needs_review" and status["pageCount"] == 1
        result = (await c.get(base + "/result")).json()["data"]
        assert result["reviewStatus"] == "needs_review"
        assert result["sourceSha256"] == doc["checksumSha256"]
        assert result["sourcePreviewAvailable"] is True
        preview = await c.get(base + "/pages/1")
        assert preview.status_code == 200 and preview.content == PNG
        assert "no-store" in preview.headers["cache-control"]
        assert (await c.get(base + "/pages/0")).status_code == 404
        assert (await c.get(base + "/pages/2")).status_code == 404
        assert (await c.get(f"/api/v1/documents/{doc['id']}")).json()["data"]["status"] == "review_required"
        assert (await c.put(base + "/result", headers=ORIGIN, json=result)).status_code == 405
        assert (await c.post(f"/api/v1/documents/{doc['id']}/extractions", headers={**ORIGIN, "Idempotency-Key": "x"}, json={"schemaVersion":"invoice-v1"})).status_code == 503
    async with app.state.session_factory() as db:
        events = list(await db.scalars(select(AuditEvent).where(AuditEvent.action.like("document.layout.%"))))
        assert len(events) == 2
        assert all(event.details == {"outcome": "succeeded"} for event in events)


async def test_source_mutation_blocks_admission_and_worker(app):
    enable(app)
    async with client(app) as c:
        doc = await prepare(app, c)
        assert (await request(c, doc)).status_code == 202
        path = next(app.state.quarantine_storage.root.glob("*.quarantine"))
        path.write_bytes(b"Z" * len(PDF))
        assert (await request(c, doc)).status_code == 409
        parser = MockParser()
        await run_once(app.state.session_factory, app.state.quarantine_storage, parser)
        assert parser.calls == 0
    async with app.state.session_factory() as db:
        job = await db.scalar(select(LayoutJob))
        assert job.state == "error" and job.result is None and job.page_previews is None
        assert (await db.scalar(select(Document))).status == "review_required"


@pytest.mark.parametrize("mutation", ["source", "scan", "lease"])
async def test_fresh_validation_and_stale_worker_guard(app, mutation):
    enable(app)
    async with client(app) as c:
        doc = await prepare(app, c)
        job = (await request(c, doc)).json()["data"]["job"]

    class MutatingParser(MockParser):
        async def parse(self, data):
            result = await super().parse(data)
            if mutation == "source":
                next(app.state.quarantine_storage.root.glob("*.quarantine")).write_bytes(b"Z" * len(PDF))
            else:
                async with app.state.session_factory() as db:
                    if mutation == "scan":
                        await db.execute(update(Document).values(scan_status="rejected"))
                    else:
                        await db.execute(update(LayoutJob).values(lease_until=now() - timedelta(seconds=1)))
                    await db.commit()
            return result
    await run_once(app.state.session_factory, app.state.quarantine_storage, MutatingParser())
    async with app.state.session_factory() as db:
        row = await db.get(LayoutJob, UUID(job["id"]))
        assert row.result is None and row.state != "needs_review"
        if mutation == "lease":
            token = row.lease_token
            assert (await claim(db))[1] != token


async def test_tenant_and_role_isolation(app):
    enable(app)
    async with client(app) as a, client(app) as b:
        doc = await prepare(app, a)
        job = (await request(a, doc)).json()["data"]["job"]
        await prepare(app, b, "otherlayout@example.com")
        base = f"/api/v1/documents/{doc['id']}/layout-jobs/{job['id']}"
        for suffix in ("", "/result", "/pages/1"):
            assert (await b.get(base + suffix)).status_code == 404
        assert (await request(b, doc)).status_code == 404
        async with app.state.session_factory() as db:
            user = await db.scalar(select(User).where(User.email == "layout@example.com"))
            user.role = "operator"
            await db.commit()
        assert (await request(a, doc)).status_code == 403


async def test_result_reads_revalidate_original(app):
    enable(app)
    async with client(app) as c:
        doc = await prepare(app, c)
        job = (await request(c, doc)).json()["data"]["job"]
        await run_once(app.state.session_factory, app.state.quarantine_storage, MockParser())
        next(app.state.quarantine_storage.root.glob("*.quarantine")).write_bytes(b"Z" * len(PDF))
        base = f"/api/v1/documents/{doc['id']}/layout-jobs/{job['id']}"
        assert (await c.get(base + "/result")).status_code == 409
        assert (await c.get(base + "/pages/1")).status_code == 409


async def test_postgres_admission_races_and_exhausted_leases(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL concurrency evidence requires PostgreSQL")
    enable(app)
    for i in range(4):
        async with client(app) as c:
            doc = await prepare(app, c, f"concurrent{i}@example.com")
            replies = await asyncio.gather(*(request(c, doc) for _ in range(3)))
            assert all(r.status_code == 202 for r in replies)
            assert len({r.json()["data"]["job"]["id"] for r in replies}) == 1
    async def take():
        async with app.state.session_factory() as db:
            return await claim(db)
    claimed = [v for v in await asyncio.gather(*(take() for _ in range(8))) if v]
    assert len(claimed) == 2 and len({v[0] for v in claimed}) == 2
    async with app.state.session_factory() as db:
        job = await db.get(LayoutJob, claimed[0][0])
        job.attempts = 3
        job.lease_until = now() - timedelta(seconds=1)
        await db.commit()
        await claim(db)
        await db.refresh(job)
        assert job.state == "error" and job.result_code == "lease_exhausted"


async def test_one_tenant_slot_and_version_binding(app):
    enable(app)
    async with client(app) as c:
        first = await prepare(app, c)
        await request(c, first)
        content = PDF + b" second synthetic file"
        second = await create(c, content)
        upload = await start(c, second, key="second")
        await put(c, upload, content)
        await finish(c, second, upload)
        async with app.state.session_factory() as db:
            row = await db.get(Document, UUID(second["id"]))
            row.scan_status = "clean"
            scan = await db.scalar(select(ScanJob).where(ScanJob.document_id == row.id))
            scan.state = "clean"
            await db.commit()
        assert (await request(c, second)).status_code == 202
        bad = await c.post(f"/api/v1/documents/{first['id']}/layout-jobs", headers=ORIGIN,
                           json={"pipelineVersion": "remote-unreviewed", "command": "anything"})
        assert bad.status_code == 400
        jobs = (await c.get(f"/api/v1/documents/{first['id']}/layout-jobs")).json()["data"]["items"]
        assert len(jobs) == 1
    async with app.state.session_factory() as db:
        assert await claim(db)
        assert await claim(db) is None


async def test_parser_failure_is_sanitized_and_never_approved(app):
    from app.domain.documents.layout_adapter import LayoutError
    enable(app)
    async with client(app) as c:
        doc = await prepare(app, c)
        await request(c, doc)

    class FailureParser:
        async def parse(self, data):
            raise LayoutError("private-document-content-must-never-leak")
    await run_once(app.state.session_factory, app.state.quarantine_storage, FailureParser())
    async with app.state.session_factory() as db:
        job = await db.scalar(select(LayoutJob))
        assert job.state == "error" and job.result_code == "parser_failed"
        assert job.result is None and job.page_previews is None
        assert (await db.scalar(select(Document))).status == "review_required"
        event = await db.scalar(select(AuditEvent).where(AuditEvent.action == "document.layout.error"))
        assert event.details == {"outcome": "failed"}
