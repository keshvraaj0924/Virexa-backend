"""Authenticated layout requests and reads. No output mutation or original download."""
import asyncio
import base64
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select

from app.core.api import success_response
from app.core.errors import ApiError
from app.core.policy import Action, require_document
from app.db.models import Organization
from app.domain.audit.service import record
from app.domain.documents.layout_models import LayoutJob
from app.domain.documents.layout_previews import validate_preview
from app.domain.documents.layout_schemas import (
    LayoutList,
    LayoutRequest,
    LayoutRequested,
    LayoutResult,
    LayoutStatus,
)
from app.domain.documents.models import ScanJob, Upload
from app.domain.documents.routes import (
    DB,
    Read,
    Write,
    attributes,
    aware,
    document,
    now,
    origin,
    storage,
    upload_slot,
)
from app.domain.documents.scan_jobs import read_snapshot
from app.domain.documents.schemas import ERROR_RESPONSES, Success

PIPELINE_VERSION = "docling-local-v1"
router = APIRouter(prefix="/documents", tags=["document layout"], responses=ERROR_RESPONSES)


def public_job(job: LayoutJob) -> dict:
    return dict(id=job.id, documentId=job.document_id, state=job.state,
                pipelineVersion=job.pipeline_version, sourceSha256=job.checksum_sha256,
                pageCount=job.page_count, failureCode=job.result_code,
                createdAt=aware(job.created_at), updatedAt=aware(job.updated_at))


@router.post("/{document_id}/layout-jobs", status_code=202,
             response_model=Success[LayoutRequested],
             dependencies=[Depends(origin), Depends(upload_slot)])
async def request_layout(document_id: UUID, data: LayoutRequest,
                         request: Request, context: Write, db: DB):
    # Admission is serialized by tenant, including the preview execution quota.
    await db.scalar(select(Organization.id).where(
        Organization.id == context.organization_id).with_for_update())
    doc = await document(db, context, document_id, lock=True)
    require_document(context, Action.LAYOUT_REQUEST, attributes(doc))
    if doc.media_type != "application/pdf":
        raise ApiError(415, "LAYOUT_PDF_REQUIRED", "Local layout currently accepts PDF only.")
    if doc.status == "completed":
        raise ApiError(409, "DOCUMENT_NOT_WRITABLE", "Completed documents cannot be reparsed.")
    settings = request.app.state.settings
    if not settings.document_layout_enabled or not settings.document_layout_assets:
        raise ApiError(503, "LAYOUT_UNAVAILABLE", "Local layout parsing is not configured.")
    adapter = storage(request)
    scan = await db.scalar(select(ScanJob).where(
        ScanJob.document_id == doc.id, ScanJob.organization_id == doc.organization_id,
        ScanJob.state == "clean", ScanJob.checksum_sha256 == doc.checksum_sha256))
    upload = await db.get(Upload, scan.upload_id) if scan else None
    if (not scan or not upload or not upload.bytes_verified or upload.status != "completed"
            or upload.organization_id != doc.organization_id or upload.document_id != doc.id):
        raise ApiError(409, "DOCUMENT_NOT_READY", "A current verified clean upload is required.")
    try:
        await asyncio.to_thread(read_snapshot, adapter, scan, doc)
    except (OSError, ValueError):
        raise ApiError(409, "LAYOUT_SOURCE_CHANGED", "Private source integrity check failed.") from None
    job = await db.scalar(select(LayoutJob).where(
        LayoutJob.document_id == doc.id, LayoutJob.pipeline_version == data.pipelineVersion))
    replayed = job is not None
    if job is None:
        count = await db.scalar(select(func.count()).select_from(LayoutJob).where(
            LayoutJob.organization_id == context.organization_id))
        if count >= 100:
            raise ApiError(409, "LAYOUT_PREVIEW_QUOTA", "Local layout preview quota is exhausted.")
        job = LayoutJob(organization_id=context.organization_id, document_id=doc.id,
                        upload_id=upload.id, actor_user_id=context.user_id,
                        checksum_sha256=doc.checksum_sha256, pipeline_version=PIPELINE_VERSION,
                        available_at=now(), created_at=now(), updated_at=now())
        db.add(job)
        doc.status = "review_required"
        doc.updated_at = now()
        await db.flush()
        doc.current_layout_job_id = job.id
        await record(db, organization_id=context.organization_id, actor_user_id=context.user_id,
                     action="document.layout.requested", request_id=request.state.request_id,
                     resource_id=job.id)
    await db.commit()
    return success_response(request, {"job": public_job(job), "replayed": replayed})


@router.get("/{document_id}/layout-jobs", response_model=Success[LayoutList])
async def list_layout(document_id: UUID, request: Request, context: Read, db: DB):
    doc = await document(db, context, document_id)
    jobs = list(await db.scalars(select(LayoutJob).where(
        LayoutJob.document_id == doc.id, LayoutJob.organization_id == context.organization_id)
        .order_by(LayoutJob.created_at.desc(), LayoutJob.id.desc()).limit(20)))
    return success_response(request, {"items": [public_job(job) for job in jobs]})


async def scoped_job(db, context, document_id, job_id):
    doc = await document(db, context, document_id)
    job = await db.scalar(select(LayoutJob).where(
        LayoutJob.id == job_id, LayoutJob.document_id == doc.id,
        LayoutJob.organization_id == context.organization_id))
    if job is None:
        raise ApiError(404, "NOT_FOUND", "Layout job was not found.")
    return doc, job


@router.get("/{document_id}/layout-jobs/{job_id}", response_model=Success[LayoutStatus])
async def get_layout(document_id: UUID, job_id: UUID, request: Request, context: Read, db: DB):
    _, job = await scoped_job(db, context, document_id, job_id)
    return success_response(request, public_job(job))


@router.get("/{document_id}/layout-jobs/{job_id}/result", response_model=Success[LayoutResult])
async def get_layout_result(document_id: UUID, job_id: UUID,
                            request: Request, context: Read, db: DB):
    doc, job = await scoped_job(db, context, document_id, job_id)
    if (job.state != "needs_review" or job.result is None
            or doc.scan_status != "clean" or doc.checksum_sha256 != job.checksum_sha256):
        raise ApiError(409, "LAYOUT_RESULT_UNAVAILABLE", "A current reviewable result is required.")
    try:
        from app.domain.documents.layout_jobs import verified_source

        await verified_source(db, job)
        await asyncio.to_thread(read_snapshot, storage(request), job, doc)
    except (OSError, ValueError):
        raise ApiError(409, "LAYOUT_SOURCE_CHANGED", "Private source integrity check failed.") from None
    return success_response(request, job.result)


@router.get("/{document_id}/layout-jobs/{job_id}/pages/{page_number}",
            response_class=Response,
            responses={200: {"content": {"image/png": {}}, "description": "Bounded source preview"}})
async def get_layout_page(document_id: UUID, job_id: UUID, page_number: int,
                          request: Request, context: Read, db: DB):
    doc, job = await scoped_job(db, context, document_id, job_id)
    if (job.state != "needs_review" or not job.page_previews or doc.scan_status != "clean"
            or doc.checksum_sha256 != job.checksum_sha256):
        raise ApiError(409, "LAYOUT_RESULT_UNAVAILABLE", "A current reviewable result is required.")
    if page_number < 1 or page_number > len(job.page_previews):
        raise ApiError(404, "NOT_FOUND", "Source preview page was not found.")
    try:
        from app.domain.documents.layout_jobs import verified_source

        await verified_source(db, job)
        await asyncio.to_thread(read_snapshot, storage(request), job, doc)
        image = base64.b64decode(job.page_previews[page_number - 1], validate=True)
        validate_preview(image)
    except (OSError, ValueError):
        raise ApiError(409, "LAYOUT_SOURCE_CHANGED", "Private source integrity check failed.") from None
    return Response(image, media_type="image/png", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": 'inline; filename="source-page.png"',
    })
