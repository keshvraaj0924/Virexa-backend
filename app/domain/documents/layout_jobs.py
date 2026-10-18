"""Durable, bounded local layout leases. PostgreSQL admission; no semantic approval."""
import asyncio
import base64
import json
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.audit.service import record
from app.domain.documents.layout_models import LayoutJob
from app.domain.documents.layout_routes import PIPELINE_VERSION
from app.domain.documents.models import Document, ScanJob, Upload
from app.domain.documents.scan_jobs import aware, now, read_snapshot
from app.domain.documents.storage import QuarantineStorage


async def audit(db: AsyncSession, job: LayoutJob, outcome: str):
    await record(db, organization_id=job.organization_id, actor_user_id=job.actor_user_id,
                 action=f"document.layout.{outcome}", request_id=str(uuid4()),
                 resource_id=job.document_id)


async def claim(db: AsyncSession) -> tuple[UUID, UUID] | None:
    """Two global slots, one per tenant, 3 attempts. Lease exceeds subprocess timeout."""
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(867392402)"))
    timestamp = now()
    exhausted = list(await db.scalars(select(LayoutJob).where(
        LayoutJob.state == "running", LayoutJob.lease_until <= timestamp,
        LayoutJob.attempts >= 3).with_for_update()))
    for job in exhausted:
        job.state, job.result_code, job.updated_at = "error", "lease_exhausted", timestamp
        job.lease_token = job.lease_until = None
        await audit(db, job, "error")
    active = and_(LayoutJob.state == "running", LayoutJob.lease_until > timestamp)
    if await db.scalar(select(func.count()).select_from(LayoutJob).where(active)) >= 2:
        await db.commit()
        return None
    busy = select(LayoutJob.organization_id).where(active)
    job = await db.scalar(select(LayoutJob).where(
        LayoutJob.pipeline_version == PIPELINE_VERSION,
        LayoutJob.attempts < 3, LayoutJob.organization_id.not_in(busy),
        or_(and_(LayoutJob.state == "pending", LayoutJob.available_at <= timestamp),
            and_(LayoutJob.state == "running", LayoutJob.lease_until <= timestamp)))
        .order_by(LayoutJob.available_at, LayoutJob.id).limit(1).with_for_update(skip_locked=True))
    if job is None:
        await db.commit()
        return None
    job.state = "running"
    job.attempts += 1
    job.lease_token, job.lease_until = uuid4(), timestamp + timedelta(seconds=240)
    job.updated_at = timestamp
    value = job.id, job.lease_token
    await db.commit()
    return value


async def verified_source(db: AsyncSession, job: LayoutJob) -> Document:
    doc = await db.get(Document, job.document_id)
    upload = await db.get(Upload, job.upload_id)
    scan = await db.scalar(select(ScanJob).where(
        ScanJob.document_id == job.document_id, ScanJob.organization_id == job.organization_id,
        ScanJob.upload_id == job.upload_id, ScanJob.state == "clean",
        ScanJob.checksum_sha256 == job.checksum_sha256))
    if (not doc or not upload or not scan or doc.organization_id != job.organization_id
            or doc.scan_status != "clean" or doc.checksum_sha256 != job.checksum_sha256
            or doc.media_type != "application/pdf" or doc.status == "completed"
            or upload.organization_id != job.organization_id or upload.document_id != doc.id
            or upload.status != "completed" or not upload.bytes_verified):
        raise ValueError("Invalid layout source")
    return doc


async def run_once(factory: async_sessionmaker, storage: QuarantineStorage, parser) -> bool:
    """A None parser is disabled and never claims work. Only this worker writes results."""
    if parser is None:
        return False
    # Optional dependencies are imported only by the explicitly configured worker.
    from app.domain.documents.layout_adapter import LayoutError

    async with factory() as db:
        lease = await claim(db)
    if lease is None:
        return False
    job_id, token = lease
    result, error, previews = None, None, None
    async with factory() as db:
        job = await db.get(LayoutJob, job_id)
        try:
            doc = await verified_source(db, job)
            data = await asyncio.to_thread(read_snapshot, storage, job, doc)
            parsed = await parser.parse(data)
            if not 0 < parsed.page_count <= 20:
                raise ValueError("Invalid page count")
            preview_bytes = parsed.previews
            if len(preview_bytes) != parsed.page_count or sum(map(len, preview_bytes)) > 8 * 1024 * 1024:
                raise ValueError("Missing or oversized source previews")
            if any(not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 1024 * 1024
                   for image in preview_bytes):
                raise ValueError("Invalid source preview")
            previews = [base64.b64encode(image).decode("ascii") for image in preview_bytes]
            result = dict(resultVersion="layout-result-v1", reviewStatus="needs_review",
                          sourceSha256=job.checksum_sha256, pipelineVersion=job.pipeline_version,
                          parserVersion=parsed.parser_version, modelVersion=parsed.model_version,
                          pageCount=parsed.page_count, sourcePreviewAvailable=True, warnings=list(parsed.warnings),
                          canonical=parsed.canonical)
            if len(json.dumps(result, allow_nan=False).encode()) > 8 * 1024 * 1024:
                raise ValueError("Result exceeds limit")
        except LayoutError as exc:
            error = exc.code
        except (OSError, ValueError, TypeError):
            error = "integrity_failed"
        await db.rollback()
    async with factory() as db:
        job = await db.scalar(select(LayoutJob).where(LayoutJob.id == job_id).with_for_update())
        if job.state != "running" or job.lease_token != token or aware(job.lease_until) <= now():
            return True  # An expired/replaced worker can never publish.
        await db.scalar(select(Document.id).where(Document.id == job.document_id).with_for_update())
        try:
            # Fresh database identity AND fresh private-file read immediately before commit.
            doc = await verified_source(db, job)
            await asyncio.to_thread(read_snapshot, storage, job, doc)
        except (OSError, ValueError):
            error = "source_changed"
        if aware(job.lease_until) <= now():
            return True
        job.updated_at = now()
        job.lease_token = job.lease_until = None
        if error:
            job.result = None
            job.page_previews = None
            job.result_code = error
            job.state = "error"  # Explicit fail-closed; only crashed leases are retried.
            await audit(db, job, "error")
        else:
            job.result = result
            job.page_previews = previews
            job.page_count = result["pageCount"]
            job.result_code = None
            job.state = "needs_review"
            doc.status, doc.updated_at = "review_required", now()
            await audit(db, job, "needs_review")
        await db.commit()
    return True
