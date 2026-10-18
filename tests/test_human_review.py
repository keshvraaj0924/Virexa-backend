"""Synthetic layout candidates, real private source integrity, no AI/semantic claims."""

# ruff: noqa: F811
import asyncio
import base64
import struct
import zlib
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update
from test_document_intake import ORIGIN, app, client, create, finish, put, signup, start  # noqa: F401

from app.core.auth_context import AuthContext
from app.core.errors import ApiError
from app.core.rbac import Role
from app.db.models import Organization, User
from app.domain.audit.models import AuditEvent
from app.domain.documents.layout_models import LayoutJob
from app.domain.documents.models import Document, ScanJob
from app.domain.documents.scan_jobs import now
from app.domain.reviews import service
from app.domain.reviews.models import ReviewEvent
from app.domain.reviews.schemas import CorrectLayout, DecideLayout


def synthetic_png():
    """Structurally valid one-pixel fixture, not a real document rendering."""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


async def candidate(app, c):
    user = await signup(c)
    doc = await create(c)
    upload = await start(c, doc)
    assert (await put(c, upload)).status_code == 204
    assert (await finish(c, doc, upload)).status_code == 200
    async with app.state.session_factory() as db:
        d = await db.get(Document, UUID(doc["id"]))
        scan = await db.scalar(select(ScanJob).where(ScanJob.document_id == d.id))
        scan.state, d.scan_status, d.status = "clean", "clean", "review_required"
        result = {
            "resultVersion": "layout-result-v1",
            "reviewStatus": "needs_review",
            "sourceSha256": d.checksum_sha256,
            "pipelineVersion": "docling-local-v1",
            "parserVersion": "synthetic",
            "modelVersion": "synthetic",
            "pageCount": 1,
            "sourcePreviewAvailable": True,
            "warnings": [],
            "canonical": {"texts": [{"self_ref": "#/texts/0", "text": "Original"}]},
        }
        job = LayoutJob(
            organization_id=d.organization_id,
            document_id=d.id,
            upload_id=scan.upload_id,
            actor_user_id=UUID(user["id"]),
            checksum_sha256=d.checksum_sha256,
            pipeline_version="docling-local-v1",
            state="needs_review",
            attempts=1,
            available_at=now(),
            created_at=now(),
            updated_at=now(),
            page_count=1,
            result=result,
            page_previews=[base64.b64encode(synthetic_png()).decode()],
        )
        db.add(job)
        await db.flush()
        d.current_layout_job_id = job.id
        await db.commit()
        return user, doc, job.id


def decision(doc, revision=0, decision="approve"):
    return {
        "expectedRevision": revision,
        "sourceSha256": doc["checksumSha256"],
        "resultKind": "layout",
        "decision": decision,
        "sourceCompared": True,
        "reasonCode": "verified_against_source" if decision == "approve" else "incorrect_layout",
    }


def correction(doc, revision, text="Corrected"):
    return {
        "expectedRevision": revision,
        "sourceSha256": doc["checksumSha256"],
        "resultKind": "layout",
        "corrections": [{"nodeId": "#/texts/0", "text": text}],
    }


async def post(c, job, endpoint, body, key=None):
    return await c.post(
        f"/api/v1/layout-jobs/{job}/review/{endpoint}",
        json=body,
        headers={**ORIGIN, "Idempotency-Key": key or str(uuid4())},
    )


async def snapshot(app, user, job, revision, kind="layout"):
    context = AuthContext(UUID(user["id"]), UUID(user["organizationId"]), Role.ORGANIZATION_ADMIN)
    async with app.state.session_factory() as db:
        return await service.approved_snapshot(
            db,
            context,
            job,
            app.state.quarantine_storage,
            result_kind=kind,
            expected_revision=revision,
        )


async def test_full_review_lifecycle_invalidation_history_and_immutable_parser(app):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        with pytest.raises(ApiError, match="explicit human approval"):
            await snapshot(app, user, job, 0)
        result = await post(
            c, job, "corrections", correction(doc, 0, "<script>plain text</script>")
        )
        assert result.status_code == 200, result.text
        assert result.json()["data"]["review"]["status"] == "corrected"
        approved = await post(c, job, "decisions", decision(doc, 1))
        assert approved.status_code == 200, approved.text
        view = await snapshot(app, user, job, 2)
        assert view["canonical"]["texts"][0]["text"] == "<script>plain text</script>"
        assert view["scope"] == "layout_only"
        with pytest.raises(ApiError, match="semantic"):
            await snapshot(app, user, job, 2, "invoice")
        async with app.state.session_factory() as db:
            original = await db.get(LayoutJob, job)
            assert original.result["canonical"]["texts"][0]["text"] == "Original"
            events = list(
                await db.scalars(
                    select(AuditEvent).where(AuditEvent.resource_type == "layout_review")
                )
            )
            assert len(events) == 2
            assert all("script" not in str(event.details) for event in events)
        assert (await post(c, job, "corrections", correction(doc, 2))).status_code == 200
        with pytest.raises(ApiError):
            await snapshot(app, user, job, 2)
        rejected = await post(c, job, "decisions", decision(doc, 3, "reject"))
        assert rejected.json()["data"]["review"]["status"] == "rejected"
        with pytest.raises(ApiError):
            await snapshot(app, user, job, 4)
        page = (await c.get(f"/api/v1/layout-jobs/{job}/review/history?limit=2")).json()["data"]
        assert [x["revision"] for x in page["items"]] == [4, 3]
        assert page["nextCursor"] == 3
        assert "corrections" not in page["items"][0]
        page2 = (await c.get(f"/api/v1/layout-jobs/{job}/review/history?cursor=3")).json()["data"]
        assert [x["revision"] for x in page2["items"]] == [2, 1]


async def test_idempotency_exactly_once_and_stale_approval(app):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        key = str(uuid4())
        body = decision(doc)
        assert (await post(c, job, "decisions", body, key)).status_code == 200
        retry = await post(c, job, "decisions", body, key)
        assert retry.json()["data"]["replayed"] is True
        assert (await post(c, job, "decisions", decision(doc, 0, "reject"), key)).status_code == 409
        assert (await post(c, job, "corrections", correction(doc, 1))).status_code == 200
        retry = await post(c, job, "decisions", body, key)
        assert retry.json()["data"]["review"]["status"] == "corrected"
        assert retry.json()["data"]["review"]["eligible"] is False
        assert (await post(c, job, "decisions", body)).status_code == 409
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(ReviewEvent)) == 2


@pytest.mark.parametrize("role", ["operator", "manager", "viewer", "auditor"])
async def test_ordinary_roles_cannot_review(app, role):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        async with app.state.session_factory() as db:
            await db.execute(update(User).where(User.id == UUID(user["id"])).values(role=role))
            await db.commit()
        assert (await post(c, job, "decisions", decision(doc))).status_code == 403
        assert (await post(c, job, "corrections", correction(doc, 0))).status_code == 403


async def test_reviewer_and_individual_admin_self_review(app):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        async with app.state.session_factory() as db:
            await db.execute(
                update(Organization)
                .where(Organization.id == UUID(user["organizationId"]))
                .values(account_type="individual")
            )
            await db.commit()
        assert (await post(c, job, "decisions", decision(doc))).status_code == 200
        async with app.state.session_factory() as db:
            await db.execute(
                update(User).where(User.id == UUID(user["id"])).values(role="reviewer")
            )
            await db.commit()
        assert (await post(c, job, "corrections", correction(doc, 1))).status_code == 200
        assert (await post(c, job, "decisions", decision(doc, 2))).status_code == 200


async def test_tenant_mismatch_and_current_database_authorization(app):
    async with client(app) as a, client(app) as b:
        user, doc, job = await candidate(app, a)
        await signup(b, "other@example.com")
        assert (await post(b, job, "decisions", decision(doc))).status_code == 404
        assert (await b.get(f"/api/v1/layout-jobs/{job}/review")).status_code == 404
        context = AuthContext(
            UUID(user["id"]), UUID(user["organizationId"]), Role.ORGANIZATION_ADMIN
        )
        async with app.state.session_factory() as db:
            await db.execute(update(User).where(User.id == context.user_id).values(role="viewer"))
            await db.commit()
        async with app.state.session_factory() as db:
            with pytest.raises(ApiError, match="reviewer"):
                await service.change(
                    db,
                    context,
                    job,
                    DecideLayout(**decision(doc)),
                    str(uuid4()),
                    str(uuid4()),
                    app.state.quarantine_storage,
                )


@pytest.mark.parametrize("mutation", ["source_hash", "source_file", "scan", "previews", "new_job"])
async def test_source_changes_and_new_candidate_invalidate_approval(app, mutation):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        assert (await post(c, job, "decisions", decision(doc))).status_code == 200
        async with app.state.session_factory() as db:
            row = await db.get(LayoutJob, job)
            document = await db.get(Document, row.document_id)
            if mutation == "source_hash":
                document.checksum_sha256 = "a" * 64
            elif mutation == "source_file":
                path = app.state.quarantine_storage.path(
                    row.organization_id, row.document_id, row.upload_id
                )
                path.write_bytes(b"changed source")
            elif mutation == "scan":
                document.scan_status = "rejected"
            elif mutation == "previews":
                row.page_previews = None
            else:
                newer = LayoutJob(
                    organization_id=row.organization_id,
                    document_id=row.document_id,
                    upload_id=row.upload_id,
                    actor_user_id=row.actor_user_id,
                    checksum_sha256=row.checksum_sha256,
                    pipeline_version="future-version",
                    state="pending",
                    attempts=0,
                    available_at=now(),
                    created_at=now() + timedelta(seconds=1),
                    updated_at=now(),
                )
                db.add(newer)
                await db.flush()
                document.current_layout_job_id = newer.id
            await db.commit()
        with pytest.raises(ApiError):
            await snapshot(app, user, job, 1)
        assert (await post(c, job, "decisions", decision(doc, 1))).status_code == 409


async def test_input_contract_forbids_bypass_and_unknown_nodes(app):
    async with client(app) as c:
        _, doc, job = await candidate(app, c)
        for extra in (
            {"verified": True},
            {"status": "approved"},
            {"resultKind": "invoice"},
            {"sourceCompared": False},
            {"expectedRevision": True},
        ):
            assert (await post(c, job, "decisions", {**decision(doc), **extra})).status_code == 400
        bad = correction(doc, 0)
        bad["corrections"][0]["nodeId"] = "#/texts/999"
        assert (await post(c, job, "corrections", bad)).status_code == 422
        with pytest.raises(ValidationError):
            CorrectLayout(
                **{
                    **correction(doc, 0),
                    "corrections": [
                        {"nodeId": f"#/texts/{i}", "text": "x" * 4000} for i in range(100)
                    ],
                }
            )


async def test_postgres_concurrent_edit_and_approval_only_one_wins(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL row locks")
    async with client(app) as c:
        _, doc, job = await candidate(app, c)
        results = await asyncio.gather(
            post(c, job, "decisions", decision(doc)),
            post(c, job, "corrections", correction(doc, 0)),
        )
        assert sorted(r.status_code for r in results) == [200, 409]
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(ReviewEvent)) == 1


async def test_postgres_concurrent_idempotent_decision(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL row locks")
    async with client(app) as c:
        _, doc, job = await candidate(app, c)
        key = str(uuid4())
        results = await asyncio.gather(
            *(post(c, job, "decisions", decision(doc), key) for _ in range(4))
        )
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        assert sum(not r.json()["data"]["replayed"] for r in results) == 1
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(ReviewEvent)) == 1


async def test_postgres_demotion_wins_before_waiting_review(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL row locks")
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        context = AuthContext(
            UUID(user["id"]), UUID(user["organizationId"]), Role.ORGANIZATION_ADMIN
        )
        started = asyncio.Event()

        async def attempt():
            async with app.state.session_factory() as db:
                started.set()
                try:
                    await service.change(
                        db,
                        context,
                        job,
                        DecideLayout(**decision(doc)),
                        str(uuid4()),
                        str(uuid4()),
                        app.state.quarantine_storage,
                    )
                except ApiError as error:
                    return error.status
                return 200

        async with app.state.session_factory() as admin:
            await admin.scalar(
                select(Organization)
                .where(Organization.id == context.organization_id)
                .with_for_update()
            )
            task = asyncio.create_task(attempt())
            await started.wait()
            await admin.execute(
                update(User).where(User.id == context.user_id).values(role="viewer")
            )
            await admin.commit()
            assert await task == 403
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(ReviewEvent)) == 0


async def test_orm_review_history_is_append_only(app):
    async with client(app) as c:
        _, doc, job = await candidate(app, c)
        assert (await post(c, job, "decisions", decision(doc))).status_code == 200
        async with app.state.session_factory() as db:
            event = await db.scalar(select(ReviewEvent))
            event.status = "rejected"
            with pytest.raises(ValueError, match="immutable"):
                await db.commit()


async def test_changed_candidate_and_unverified_upload_cannot_be_reapproved(app):
    from app.domain.documents.models import Upload

    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        assert (await post(c, job, "decisions", decision(doc))).status_code == 200
        async with app.state.session_factory() as db:
            row = await db.get(LayoutJob, job)
            changed = {**row.result, "warnings": ["altered"]}
            row.result = changed
            await db.commit()
        assert (await post(c, job, "decisions", decision(doc, 1))).status_code == 409
        with pytest.raises(ApiError):
            await snapshot(app, user, job, 1)
        async with app.state.session_factory() as db:
            row = await db.get(LayoutJob, job)
            upload = await db.get(Upload, row.upload_id)
            upload.bytes_verified = False
            await db.commit()
        view = (await c.get(f"/api/v1/layout-jobs/{job}/review")).json()["data"]
        assert view["ready"] is False and view["eligible"] is False


async def test_postgres_migration_trigger_blocks_direct_history_mutation(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL migration trigger")
    from sqlalchemy import delete, text
    from sqlalchemy.exc import DBAPIError

    async with app.state.session_factory() as db:
        installed = await db.scalar(text("SELECT to_regprocedure('prevent_review_mutation()')"))
        if installed is None:
            pytest.skip("Run Alembic upgrade head before this migration integration test")
        # The test fixture rebuilds tables, so restore the migration's trigger.
        await db.execute(
            text(
                "CREATE TRIGGER review_history_immutable BEFORE UPDATE OR DELETE "
                "ON document_review_events FOR EACH ROW "
                "EXECUTE FUNCTION prevent_review_mutation()"
            )
        )
        await db.commit()
    async with client(app) as c:
        _, doc, job = await candidate(app, c)
        assert (await post(c, job, "decisions", decision(doc))).status_code == 200
    async with app.state.session_factory() as db:
        with pytest.raises(DBAPIError, match="immutable"):
            await db.execute(update(ReviewEvent).values(status="rejected"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="immutable"):
            await db.execute(delete(ReviewEvent))
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(ReviewEvent)) == 1


async def test_unavailable_source_can_be_rejected_but_never_approved(app):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        async with app.state.session_factory() as db:
            row = await db.get(LayoutJob, job)
            row.page_previews = None
            await db.commit()
        assert (await post(c, job, "decisions", decision(doc))).status_code == 409
        reject = {
            **decision(doc, 0, "reject"),
            "sourceCompared": False,
            "reasonCode": "unreadable_source",
        }
        result = await post(c, job, "decisions", reject)
        assert result.status_code == 200, result.text
        assert result.json()["data"]["review"]["status"] == "rejected"
        assert not result.json()["data"]["review"]["eligible"]
        with pytest.raises(ApiError):
            await snapshot(app, user, job, 1)


async def test_reject_damaged_evidence_does_not_reanchor_approval(app):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        assert (await post(c, job, "decisions", decision(doc))).status_code == 200
        async with app.state.session_factory() as db:
            row = await db.get(LayoutJob, job)
            row.page_previews = None
            await db.commit()
        reject = {
            **decision(doc, 1, "reject"),
            "sourceCompared": False,
            "reasonCode": "unreadable_source",
        }
        assert (await post(c, job, "decisions", reject)).status_code == 200
        with pytest.raises(ApiError):
            await snapshot(app, user, job, 2)
        async with app.state.session_factory() as db:
            row = await db.get(LayoutJob, job)
            row.page_previews = [base64.b64encode(synthetic_png()).decode()]
            row.result = {**row.result, "warnings": ["changed candidate"]}
            await db.commit()
        assert (await post(c, job, "decisions", decision(doc, 2))).status_code == 409


@pytest.mark.parametrize("preview", ["iVBORw0KGgo=", "not-base64", "", "a" * 1400001])
async def test_invalid_source_preview_never_supports_approval(app, preview):
    async with client(app) as c:
        _, doc, job = await candidate(app, c)
        async with app.state.session_factory() as db:
            row = await db.get(LayoutJob, job)
            row.page_previews = [preview]
            await db.commit()
        assert (await post(c, job, "decisions", decision(doc))).status_code == 409
        state = (await c.get(f"/api/v1/layout-jobs/{job}/review")).json()["data"]
        assert state["ready"] is False and state["eligible"] is False
        rejected = await post(
            c,
            job,
            "decisions",
            {
                **decision(doc, 0, "reject"),
                "sourceCompared": False,
                "reasonCode": "unreadable_source",
            },
        )
        assert rejected.status_code == 200


async def test_audit_resource_resolves_exact_redacted_review_event(app):
    async with client(app) as c, client(app) as other:
        _, doc, job = await candidate(app, c)
        await signup(other, "audit-other@example.com")
        assert (
            await post(c, job, "corrections", correction(doc, 0, "private correction"))
        ).status_code == 200
        assert (await post(c, job, "decisions", decision(doc, 1))).status_code == 200
        async with app.state.session_factory() as db:
            audit = await db.scalar(
                select(AuditEvent).where(AuditEvent.action == "document.review.approved")
            )
            identity = audit.resource_id
            assert audit.resource_type == "layout_review"
        url = f"/api/v1/layout-review-events/{identity}"
        response = await c.get(url)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["jobId"] == str(job)
        assert response.json()["data"]["documentId"] == doc["id"]
        assert response.json()["data"]["revision"] == 2
        assert response.json()["data"]["sourceCompared"] is True
        assert "private correction" not in response.text
        assert (await other.get(url)).status_code == 404


async def test_rejected_revision_requires_explicit_correction_before_approval(app):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        assert (await post(c, job, "decisions", decision(doc, 0, "reject"))).status_code == 200
        attempt = await post(c, job, "decisions", decision(doc, 1))
        assert attempt.status_code == 409
        assert attempt.json()["error"]["code"] == "CORRECTION_REQUIRED"
        with pytest.raises(ApiError):
            await snapshot(app, user, job, 1)
        assert (await post(c, job, "corrections", correction(doc, 1))).status_code == 200
        assert (await post(c, job, "decisions", decision(doc, 2))).status_code == 200
        assert (await snapshot(app, user, job, 3))["eligible"] is True


async def test_noop_correction_does_not_reopen_rejected_revision(app):
    async with client(app) as c:
        _, doc, job = await candidate(app, c)
        assert (await post(c, job, "decisions", decision(doc, 0, "reject"))).status_code == 200
        unchanged = await post(c, job, "corrections", correction(doc, 1, "Original"))
        assert unchanged.status_code == 409
        assert unchanged.json()["error"]["code"] == "NO_CORRECTION_CHANGE"
        assert (await post(c, job, "decisions", decision(doc, 1))).status_code == 409
        state = (await c.get(f"/api/v1/layout-jobs/{job}/review")).json()["data"]
        assert state["revision"] == 1 and state["status"] == "rejected"


@pytest.mark.parametrize("offset", [-10, 0])
async def test_current_pointer_invalidates_approval_with_backdated_or_tied_clock(app, offset):
    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        assert (await post(c, job, "decisions", decision(doc))).status_code == 200
        async with app.state.session_factory() as db:
            old = await db.get(LayoutJob, job)
            document = await db.scalar(
                select(Document).where(Document.id == old.document_id).with_for_update()
            )
            newer = LayoutJob(
                organization_id=old.organization_id,
                document_id=old.document_id,
                upload_id=old.upload_id,
                actor_user_id=old.actor_user_id,
                checksum_sha256=old.checksum_sha256,
                pipeline_version="future-clock-independent",
                state="pending",
                attempts=0,
                available_at=now(),
                created_at=old.created_at + timedelta(seconds=offset),
                updated_at=now(),
            )
            db.add(newer)
            await db.flush()
            document.current_layout_job_id = newer.id
            await db.commit()
        with pytest.raises(ApiError):
            await snapshot(app, user, job, 1)
        state = (await c.get(f"/api/v1/layout-jobs/{job}/review")).json()["data"]
        assert state["current"] is False and state["eligible"] is False


async def test_postgres_revocation_while_waiting_never_approves(app, monkeypatch):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL resource locks")
    from app.core.authentication import AuthenticationService

    entered = asyncio.Event()
    original = service.locked

    async def observed(*args, **kwargs):
        entered.set()
        return await original(*args, **kwargs)

    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        monkeypatch.setattr(service, "locked", observed)
        async with app.state.session_factory() as blocker:
            await blocker.scalar(
                select(Organization)
                .where(Organization.id == UUID(user["organizationId"]))
                .with_for_update(key_share=True)
            )
            task = asyncio.create_task(post(c, job, "decisions", decision(doc)))
            await entered.wait()
            async with app.state.session_factory() as revoke:
                assert await AuthenticationService().revoke(
                    revoke,
                    c.cookies.get(app.state.settings.session_cookie_name),
                    request_id=str(uuid4()),
                )
            await blocker.commit()
            response = await task
            assert response.status_code == 401, response.text
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(ReviewEvent)) == 0


async def test_postgres_approval_and_revocation_linearize_without_deadlock(app, monkeypatch):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL authority locks")
    from app.core.authentication import AuthenticationService

    in_audit, release, revoke_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = service.record

    async def pause_record(*args, **kwargs):
        if kwargs["action"] == "document.review.approved":
            in_audit.set()
            await release.wait()
        return await original(*args, **kwargs)

    async with client(app) as c:
        _, doc, job = await candidate(app, c)
        monkeypatch.setattr(service, "record", pause_record)
        approve = asyncio.create_task(post(c, job, "decisions", decision(doc)))
        await in_audit.wait()

        async def revoke():
            async with app.state.session_factory() as db:
                revoke_started.set()
                return await AuthenticationService().revoke(
                    db,
                    c.cookies.get(app.state.settings.session_cookie_name),
                    request_id=str(uuid4()),
                )

        revocation = asyncio.create_task(revoke())
        await revoke_started.wait()
        await asyncio.sleep(0.05)
        assert not revocation.done()  # It cannot revoke between authority check and commit.
        release.set()
        approved, revoked = await asyncio.wait_for(asyncio.gather(approve, revocation), timeout=5)
        assert approved.status_code == 200 and revoked is True
        assert (await c.get(f"/api/v1/layout-jobs/{job}/review")).status_code == 401


async def test_postgres_session_expiry_while_waiting_blocks_approval(app, monkeypatch):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL resource locks")
    from app.db.models import Session

    entered = asyncio.Event()
    original = service.locked

    async def observed(*args, **kwargs):
        entered.set()
        return await original(*args, **kwargs)

    async with client(app) as c:
        user, doc, job = await candidate(app, c)
        monkeypatch.setattr(service, "locked", observed)
        async with app.state.session_factory() as blocker:
            await blocker.scalar(
                select(Organization)
                .where(Organization.id == UUID(user["organizationId"]))
                .with_for_update(key_share=True)
            )
            task = asyncio.create_task(post(c, job, "decisions", decision(doc)))
            await entered.wait()
            async with app.state.session_factory() as expired:
                await expired.execute(
                    update(Session)
                    .where(Session.user_id == UUID(user["id"]))
                    .values(expires_at=now() - timedelta(seconds=1))
                )
                await expired.commit()
            await blocker.commit()
            response = await task
            assert response.status_code == 401, response.text
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(ReviewEvent)) == 0
