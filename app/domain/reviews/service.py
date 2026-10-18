"""Version-bound human review and a fail-closed layout-only consumption gate.

All operations acquire the organization lock used by role writers, reread the
actor, then acquire the document lock used by parser enqueue/finalize. Never
lock a parser job after the document: the worker locks in the reverse order.
The caller owns the transaction for approved_snapshot and must retain its locks
until it has persisted any downstream intent referencing this exact snapshot.
"""

import asyncio
import base64
import copy
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.auth_context import AuthContext
from app.core.errors import ApiError
from app.core.policy import Action, require_action
from app.core.rbac import Role
from app.db.models import Organization, Session, User
from app.domain.audit.service import record
from app.domain.documents.layout_jobs import verified_source
from app.domain.documents.layout_models import LayoutJob
from app.domain.documents.layout_previews import validate_preview
from app.domain.documents.models import Document, Upload
from app.domain.documents.scan_jobs import read_snapshot
from app.domain.reviews.models import ReviewEvent
from app.domain.reviews.schemas import CorrectLayout, DecideLayout

REVIEW_ROLES = {"platform_admin", "tenant_admin", "organization_admin", "reviewer"}
MAX_EVENTS = 1000
MAX_CORRECTED_NODES = 1000


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def candidate_digest(job):
    """Bind the decision to both canonical output and exact source-preview evidence."""
    return digest({"result": job.result, "previews": job.page_previews})


def conflict():
    return ApiError(
        409, "REVIEW_CONFLICT", "The source or review changed. Reload before reviewing."
    )


async def locked(db, context, job_id, storage, *, write=False):
    organization = await db.scalar(
        select(Organization)
        .where(Organization.id == context.organization_id)
        .with_for_update(key_share=True)  # PostgreSQL FOR NO KEY UPDATE, FK-safe.
        .execution_options(populate_existing=True)
    )
    actor = await db.scalar(
        select(User)
        .where(User.id == context.user_id, User.organization_id == context.organization_id)
        .with_for_update(key_share=True)  # PostgreSQL FOR NO KEY UPDATE, FK-safe.
        .execution_options(populate_existing=True)
    )
    if not organization or not actor:
        raise ApiError(403, "FORBIDDEN", "Workspace access is no longer available.")
    try:
        fresh = AuthContext(
            actor.id,
            actor.organization_id,
            Role(actor.role),
            organization.account_type,
            session_id=context.session_id,
        )
    except ValueError:
        raise ApiError(403, "FORBIDDEN", "Insufficient permissions.") from None
    require_action(fresh, Action.DOCUMENT_READ)
    if write and actor.role not in REVIEW_ROLES:
        raise ApiError(403, "FORBIDDEN", "An authorized reviewer is required.")
    job = await db.scalar(
        select(LayoutJob)
        .where(LayoutJob.id == job_id, LayoutJob.organization_id == fresh.organization_id)
        .execution_options(populate_existing=True)
    )
    if not job:
        raise ApiError(404, "NOT_FOUND", "Layout candidate was not found.")
    document = await db.scalar(
        select(Document)
        .where(Document.id == job.document_id, Document.organization_id == fresh.organization_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # The worker may have committed while the document lock was being acquired.
    await db.refresh(job)
    if not document:
        raise ApiError(404, "NOT_FOUND", "Document was not found.")
    current = (
        document.current_layout_job_id == job.id and document.checksum_sha256 == job.checksum_sha256
    )
    ready = (
        job.state == "needs_review"
        and isinstance(job.result, dict)
        and document.scan_status == "clean"
        and job.result.get("sourceSha256") == document.checksum_sha256
        and job.result.get("resultVersion") == "layout-result-v1"
        and job.result.get("sourcePreviewAvailable") is True
        and bool(job.page_previews)
        and len(job.page_previews) == job.page_count
    )
    if ready:
        try:
            for encoded in job.page_previews:
                validate_preview(base64.b64decode(encoded, validate=True))
            await db.scalar(
                select(Upload)
                .where(Upload.id == job.upload_id)
                .execution_options(populate_existing=True)
            )
            await verified_source(db, job)
            await asyncio.to_thread(read_snapshot, storage, job, document)
        except (OSError, ValueError, TypeError):
            ready = False
    events = list(
        await db.scalars(
            select(ReviewEvent)
            .where(
                ReviewEvent.job_id == job.id, ReviewEvent.organization_id == fresh.organization_id
            )
            .order_by(ReviewEvent.revision.desc())
            .limit(1)
        )
    )
    # Revalidate after all potentially blocking resource locks and evidence reads.
    # The owner lock above is shared with logout/revoke-others/revoke-one.
    if context.session_id is not None:
        active = await db.scalar(
            select(Session.id)
            .where(
                Session.id == context.session_id,
                Session.user_id == actor.id,
                Session.organization_id == fresh.organization_id,
                Session.revoked_at.is_(None),
                Session.expires_at > datetime.now(timezone.utc),
            )
            .with_for_update()
        )
        if active is None:
            raise ApiError(401, "UNAUTHENTICATED", "The review session is no longer active.")
    elif write:
        raise ApiError(401, "UNAUTHENTICATED", "Human review requires an active session.")
    return fresh, job, current, ready, events


def public(job, current, ready, events):
    latest = events[-1] if events else None
    corrections = latest.corrections if latest else []
    return {
        "jobId": str(job.id),
        "documentId": str(job.document_id),
        "resultKind": "layout",
        "sourceSha256": job.checksum_sha256,
        "revision": latest.revision if latest else 0,
        "status": latest.status if latest else "needs_review",
        "corrections": corrections,
        "current": current,
        "ready": ready,
        "scope": "layout_only",
        "eligible": bool(
            current
            and ready
            and latest
            and latest.status == "approved"
            and latest.result_sha256 == candidate_digest(job)
        ),
    }


async def get(db, context, job_id, storage):
    _, job, current, ready, events = await locked(db, context, job_id, storage)
    return public(job, current, ready, events)


async def change(db, context, job_id, data, key, request_id, storage):
    actor, job, current, ready, events = await locked(db, context, job_id, storage, write=True)
    body_hash = digest(
        {
            "actorId": str(actor.user_id),
            "data": data.model_dump(mode="json"),
            "operation": "correct" if isinstance(data, CorrectLayout) else "decide",
        }
    )
    replay = await db.scalar(
        select(ReviewEvent).where(
            ReviewEvent.job_id == job.id,
            ReviewEvent.organization_id == actor.organization_id,
            ReviewEvent.idempotency_key == key,
        )
    )
    if replay:
        if replay.request_sha256 != body_hash:
            raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "Key was used for a different request.")
        # A retry never re-approves later edits. Return current authoritative state.
        return public(job, current, ready, events), True
    latest = events[-1] if events else None
    revision = latest.revision if latest else 0
    if not current or data.sourceSha256 != job.checksum_sha256 or data.expectedRevision != revision:
        raise conflict()
    rejecting = isinstance(data, DecideLayout) and data.decision == "reject"
    if (
        latest
        and latest.status == "rejected"
        and isinstance(data, DecideLayout)
        and data.decision == "approve"
    ):
        raise ApiError(
            409,
            "CORRECTION_REQUIRED",
            "A rejected revision must be corrected or reparsed before approval.",
        )
    if not ready and not rejecting:
        raise ApiError(409, "REVIEW_NOT_READY", "A current, scanned layout result is required.")
    if job.state != "needs_review" or not isinstance(job.result, dict):
        raise ApiError(409, "REVIEW_NOT_READY", "A completed layout candidate is required.")
    anchor = await db.scalar(
        select(ReviewEvent.result_sha256)
        .where(ReviewEvent.job_id == job.id, ReviewEvent.organization_id == actor.organization_id)
        .order_by(ReviewEvent.revision)
        .limit(1)
    )
    if anchor and anchor != candidate_digest(job) and not rejecting:
        raise conflict()
    if revision >= MAX_EVENTS:
        raise ApiError(409, "REVIEW_LIMIT_REACHED", "This candidate reached its review limit.")
    corrections = {item["nodeId"]: item["text"] for item in latest.corrections} if latest else {}
    if isinstance(data, CorrectLayout):
        texts = job.result.get("canonical", {}).get("texts", [])
        nodes = {
            node.get("self_ref"): node.get("text", "") for node in texts if isinstance(node, dict)
        }
        if any(item.nodeId not in nodes for item in data.corrections):
            raise ApiError(
                422, "INVALID_LAYOUT_NODE", "Correction must target an existing text node."
            )
        if all(
            item.text == corrections.get(item.nodeId, nodes[item.nodeId])
            for item in data.corrections
        ):
            raise ApiError(409, "NO_CORRECTION_CHANGE", "At least one text correction must change.")
        corrections.update({item.nodeId: item.text for item in data.corrections})
        if (
            len(corrections) > MAX_CORRECTED_NODES
            or len(json.dumps(corrections, ensure_ascii=False).encode()) > 262144
        ):
            raise ApiError(422, "CORRECTION_LIMIT_REACHED", "Too many corrected text nodes.")
        status, reason = "corrected", "human_text_correction"
    else:
        status = "approved" if data.decision == "approve" else "rejected"
        reason = data.reasonCode
    event = ReviewEvent(
        organization_id=actor.organization_id,
        job_id=job.id,
        actor_user_id=actor.user_id,
        revision=revision + 1,
        result_kind="layout",
        source_sha256=job.checksum_sha256,
        result_sha256=candidate_digest(job),
        status=status,
        reason_code=reason,
        source_compared=data.sourceCompared if isinstance(data, DecideLayout) else False,
        corrections=[{"nodeId": node, "text": text} for node, text in sorted(corrections.items())],
        idempotency_key=key,
        request_sha256=body_hash,
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    await db.flush()
    await record(
        db,
        organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
        action=f"document.review.{status}",
        resource_id=event.id,
        request_id=request_id,
    )
    result = public(job, current, ready, [*events, event])
    await db.commit()
    return result, False


async def history(db, context, job_id, query, storage):
    await locked(db, context, job_id, storage)
    statement = select(ReviewEvent).where(
        ReviewEvent.job_id == job_id, ReviewEvent.organization_id == context.organization_id
    )
    if query.cursor is not None:
        statement = statement.where(ReviewEvent.revision < query.cursor)
    rows = list(
        await db.scalars(statement.order_by(ReviewEvent.revision.desc()).limit(query.limit + 1))
    )
    visible = rows[: query.limit]
    return {
        "items": [
            {
                "id": str(event.id),
                "revision": event.revision,
                "actorUserId": str(event.actor_user_id),
                "createdAt": (
                    event.created_at.replace(tzinfo=timezone.utc)
                    if event.created_at.tzinfo is None
                    else event.created_at
                )
                .astimezone(timezone.utc)
                .isoformat(),
                "status": event.status,
                "reasonCode": event.reason_code,
                "sourceCompared": event.source_compared,
                "sourceSha256": event.source_sha256,
                "resultSha256": event.result_sha256,
                "resultKind": event.result_kind,
                "correctedNodeCount": len(event.corrections),
            }
            for event in visible
        ],
        "nextCursor": visible[-1].revision if len(rows) > query.limit else None,
    }


async def approved_snapshot(db, context, job_id, storage, *, result_kind, expected_revision):
    """Only layout consumers may call this. Approval never authorizes invoice/ERP/payment work.

    This function does not commit. Persist downstream intent in this transaction,
    retaining the document lock; revalidate at consumption, never cache eligibility.
    """
    if result_kind != "layout":
        raise ApiError(
            409, "RESULT_KIND_NOT_APPROVED", "Layout review cannot approve semantic output."
        )
    _, job, current, ready, events = await locked(db, context, job_id, storage)
    result = public(job, current, ready, events)
    if not result["eligible"] or result["revision"] != expected_revision:
        raise ApiError(
            409, "HUMAN_REVIEW_REQUIRED", "Current revision needs explicit human approval."
        )
    canonical = copy.deepcopy(job.result["canonical"])
    corrections = {item["nodeId"]: item["text"] for item in events[-1].corrections}
    for node in canonical.get("texts", []):
        if node.get("self_ref") in corrections:
            node["text"] = corrections[node["self_ref"]]
    return {
        **result,
        "canonical": canonical,
        "approvalId": str(events[-1].id),
        "resultSha256": events[-1].result_sha256,
    }


async def event_reference(db, context, event_id, storage):
    """Resolve a generic audit resource ID without disclosing correction content."""
    event = await db.scalar(
        select(ReviewEvent).where(
            ReviewEvent.id == event_id, ReviewEvent.organization_id == context.organization_id
        )
    )
    if event is None:
        raise ApiError(404, "NOT_FOUND", "Review event was not found.")
    _, job, current, _, _ = await locked(db, context, event.job_id, storage)
    return {
        "id": str(event.id),
        "jobId": str(job.id),
        "documentId": str(job.document_id),
        "currentCandidate": current,
        "revision": event.revision,
        "actorUserId": str(event.actor_user_id),
        "createdAt": (
            event.created_at.replace(tzinfo=timezone.utc)
            if event.created_at.tzinfo is None
            else event.created_at
        )
        .astimezone(timezone.utc)
        .isoformat(),
        "status": event.status,
        "reasonCode": event.reason_code,
        "sourceCompared": event.source_compared,
        "sourceSha256": event.source_sha256,
        "resultSha256": event.result_sha256,
        "resultKind": event.result_kind,
        "correctedNodeCount": len(event.corrections),
    }
