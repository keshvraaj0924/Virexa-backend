"""Tenant-scoped metadata and quarantine intake; no extraction worker is claimed."""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api import success_response
from app.core.auth_context import AuthContext, require_capability
from app.core.errors import ApiError
from app.core.policy import Action, DocumentAttributes, require_action, require_document
from app.db.base import get_db_session
from app.db.models import Organization
from app.domain.documents.models import Document, Upload
from app.domain.documents.pagination import decode_document_cursor, encode_document_cursor
from app.domain.documents.schemas import (
    ERROR_RESPONSES,
    CompletedUpload,
    CreateDocument,
    CreateExtraction,
    DocumentList,
    DocumentRecord,
    InitiatedUpload,
    ListQuery,
    Success,
)
from app.domain.documents.storage import QuarantineStorage
from app.domain.usage.service import reserve, settle

router = APIRouter(prefix="/documents", tags=["documents"], responses=ERROR_RESPONSES)
Read = Annotated[AuthContext, Depends(require_capability(Action.DOCUMENT_READ))]
Write = Annotated[AuthContext, Depends(require_capability(Action.DOCUMENT_UPLOAD))]
DB = Annotated[AsyncSession, Depends(get_db_session)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def origin(request: Request) -> None:
    if request.headers.get("origin") not in request.app.state.settings.origins:
        raise ApiError(403, "UNTRUSTED_ORIGIN", "Request origin is not trusted.")


async def upload_slot(request: Request):
    limiter = request.app.state.upload_slots
    try:
        limiter.acquire_nowait()
    except anyio.WouldBlock:
        raise ApiError(429, "UPLOAD_CAPACITY", "Upload capacity is busy; retry later.") from None
    try:
        with anyio.fail_after(request.app.state.upload_timeout_seconds):
            yield
    except TimeoutError:
        raise ApiError(408, "UPLOAD_TIMEOUT", "Upload exceeded its time limit.") from None
    finally:
        limiter.release()


def storage(request: Request) -> QuarantineStorage:
    value = request.app.state.quarantine_storage
    if value is None:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Private upload storage is not configured.")
    return value


def public_document(row: Document) -> dict[str, object]:
    return {
        "id": row.id,
        "organizationId": row.organization_id,
        "branchId": None,
        "departmentId": None,
        "source": row.source,
        "externalReference": row.external_reference,
        "originalFileName": row.original_file_name,
        "mediaType": row.media_type,
        "sizeBytes": row.size_bytes,
        "checksumSha256": row.checksum_sha256,
        "status": row.status,
        "failureCode": None,
        "scanStatus": row.scan_status,
        "receivedAt": aware(row.received_at),
        "updatedAt": aware(row.updated_at),
    }


def public_attempt(row: Upload) -> dict[str, object]:
    return {
        "id": row.id,
        "documentId": row.document_id,
        "status": row.status,
        "createdAt": aware(row.created_at),
        "updatedAt": aware(row.updated_at),
        "completedAt": aware(row.completed_at) if row.completed_at else None,
        "failureCode": None,
    }


async def document(
    db: AsyncSession, context: AuthContext, document_id: UUID, lock: bool = False
) -> Document:
    query = select(Document).where(
        Document.id == document_id, Document.organization_id == context.organization_id
    )
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Document was not found.")
    require_document(context, Action.DOCUMENT_READ, attributes(row))
    return row


def attributes(row: Document) -> DocumentAttributes:
    return DocumentAttributes(row.organization_id, row.status, row.scan_status)


async def attempt(
    db: AsyncSession, context: AuthContext, document_id: UUID, attempt_id: UUID
) -> Upload:
    row = await db.scalar(
        select(Upload)
        .where(
            Upload.id == attempt_id,
            Upload.document_id == document_id,
            Upload.organization_id == context.organization_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Upload attempt was not found.")
    return row


@router.post(
    "",
    status_code=201,
    response_model=Success[DocumentRecord],
    dependencies=[Depends(origin), Depends(upload_slot)],
)
async def create(data: CreateDocument, request: Request, context: Write, db: DB):
    require_action(context, Action.DOCUMENT_CREATE)
    if data.branchId or data.departmentId:
        raise ApiError(
            409, "HIERARCHY_UNAVAILABLE", "Branch and department intake is not implemented."
        )
    if data.source != "upload":
        raise ApiError(409, "SOURCE_UNAVAILABLE", "Only direct upload intake is implemented.")
    # PostgreSQL row lock serializes quota reservations within this tenant.
    await db.scalar(
        select(Organization.id).where(Organization.id == context.organization_id).with_for_update()
    )
    count, total = (
        await db.execute(
            select(func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0)).where(
                Document.organization_id == context.organization_id
            )
        )
    ).one()
    if count >= 1000 or total + data.sizeBytes > 1024 * 1024 * 1024:
        raise ApiError(409, "DOCUMENT_QUOTA_EXCEEDED", "Document intake quota is exhausted.")
    row = Document(
        organization_id=context.organization_id,
        original_file_name=data.originalFileName,
        media_type=data.mediaType,
        size_bytes=data.sizeBytes,
        checksum_sha256=data.checksumSha256,
        external_reference=data.externalReference,
        source=data.source,
        received_at=now(),
        updated_at=now(),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        checksum = await db.scalar(
            select(Document.id).where(
                Document.organization_id == context.organization_id,
                Document.checksum_sha256 == data.checksumSha256,
            )
        )
        reference = (
            await db.scalar(
                select(Document.id).where(
                    Document.organization_id == context.organization_id,
                    Document.external_reference == data.externalReference,
                )
            )
            if data.externalReference
            else None
        )
        if checksum or reference:
            code = (
                "DOCUMENT_CHECKSUM_CONFLICT" if checksum else "DOCUMENT_EXTERNAL_REFERENCE_CONFLICT"
            )
            raise ApiError(409, code, "Document metadata already exists.") from None
        raise
    return success_response(request, public_document(row))


@router.get("", response_model=Success[DocumentList])
async def listing(request: Request, context: Read, db: DB, query: Annotated[ListQuery, Query()]):
    statement = select(Document).where(Document.organization_id == context.organization_id)
    if query.status:
        statement = statement.where(Document.status == query.status)
    if query.cursor:
        received_at, document_id = decode_document_cursor(
            query.cursor, organization_id=context.organization_id, status=query.status
        )
        statement = statement.where(
            or_(
                Document.received_at < received_at,
                and_(Document.received_at == received_at, Document.id < document_id),
            )
        )
    rows = list(
        await db.scalars(
            statement.order_by(Document.received_at.desc(), Document.id.desc()).limit(
                query.limit + 1
            )
        )
    )
    cursor = None
    if len(rows) > query.limit:
        rows = rows[: query.limit]
        tail = rows[-1]
        cursor = encode_document_cursor(
            organization_id=context.organization_id,
            status=query.status,
            received_at=aware(tail.received_at),
            document_id=tail.id,
        )
    return success_response(
        request, {"items": [public_document(row) for row in rows], "nextCursor": cursor}
    )


@router.get("/{document_id}", response_model=Success[DocumentRecord])
async def get(document_id: UUID, request: Request, context: Read, db: DB):
    return success_response(request, public_document(await document(db, context, document_id)))


@router.post(
    "/{document_id}/uploads",
    response_model=Success[InitiatedUpload],
    dependencies=[Depends(origin), Depends(upload_slot)],
)
async def initiate(document_id: UUID, request: Request, context: Write, db: DB, key: Key):
    doc = await document(db, context, document_id, lock=True)
    storage(request)
    if not key.strip() or key != key.strip():
        raise ApiError(400, "VALIDATION_ERROR", "Idempotency key is invalid.")
    row = await db.scalar(
        select(Upload).where(
            Upload.organization_id == context.organization_id, Upload.idempotency_key == key
        )
    )
    replayed = row is not None
    if row and row.document_id != doc.id:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency key belongs to another request.")
    if row is None:
        require_document(context, Action.DOCUMENT_UPLOAD, attributes(doc))
        existing = await db.scalar(
            select(Upload.id).where(
                Upload.organization_id == context.organization_id, Upload.document_id == doc.id
            )
        )
        if existing:
            raise ApiError(409, "UPLOAD_EXISTS", "Use the original upload idempotency key.")
        row = Upload(
            organization_id=context.organization_id,
            document_id=doc.id,
            idempotency_key=key,
            created_at=now(),
            updated_at=now(),
            expires_at=now() + timedelta(minutes=15),
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise ApiError(409, "UPLOAD_CONFLICT", "Retry the original upload request.") from None
    if row.status != "completed":
        require_document(context, Action.DOCUMENT_UPLOAD, attributes(doc))
        if row.status != "initiated":
            raise ApiError(409, "UPLOAD_NOT_WRITABLE", "Upload attempt is not writable.")
    if aware(row.expires_at) <= now() and row.status != "completed":
        # The same authenticated request can renew its unfinished attempt; no new object.
        row.expires_at = now() + timedelta(minutes=15)
    await db.commit()
    base = request.app.state.settings.public_api_origin
    url = (
        f"{base}{request.app.state.settings.api_prefix}/documents/{doc.id}/uploads/{row.id}/content"
    )
    return success_response(
        request,
        {
            "attempt": public_attempt(row),
            "replayed": replayed,
            "target": {
                "uploadUrl": url,
                "expiresAt": aware(row.expires_at),
                "requiredHeaders": {"Content-Type": doc.media_type},
            },
        },
    )


@router.put(
    "/{document_id}/uploads/{attempt_id}/content",
    status_code=204,
    dependencies=[Depends(origin), Depends(upload_slot)],
)
async def upload_content(
    document_id: UUID, attempt_id: UUID, request: Request, context: Write, db: DB
):
    doc = await document(db, context, document_id, lock=True)
    row = await attempt(db, context, document_id, attempt_id)
    require_document(context, Action.DOCUMENT_UPLOAD, attributes(doc))
    adapter = storage(request)
    if aware(row.expires_at) <= now() or row.status != "initiated":
        raise ApiError(
            409, "UPLOAD_NOT_WRITABLE", "Upload attempt is expired or already completed."
        )
    if request.headers.get("content-type") != doc.media_type:
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Upload media type does not match metadata.")
    path = adapter.path(context.organization_id, doc.id, row.id)
    try:
        with anyio.fail_after(60):
            await adapter.write(
                path, request.stream(), doc.size_bytes, doc.checksum_sha256, doc.media_type
            )
    except TimeoutError:
        raise ApiError(408, "UPLOAD_TIMEOUT", "Upload exceeded its time limit.") from None
    except OSError:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Private upload storage is unavailable."
        ) from None
    row.bytes_verified = True
    row.updated_at = now()
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/{document_id}/uploads/{attempt_id}/complete",
    response_model=Success[CompletedUpload],
    dependencies=[Depends(origin), Depends(upload_slot)],
)
async def complete(document_id: UUID, attempt_id: UUID, request: Request, context: Write, db: DB):
    doc = await document(db, context, document_id, lock=True)
    row = await attempt(db, context, document_id, attempt_id)
    adapter = storage(request)
    replayed = row.status == "completed"
    if not replayed:
        require_document(context, Action.DOCUMENT_UPLOAD, attributes(doc))
        if row.status != "initiated" or aware(row.expires_at) <= now():
            raise ApiError(409, "UPLOAD_NOT_WRITABLE", "Upload attempt is expired or not writable.")
    if not row.bytes_verified:
        raise ApiError(409, "UPLOAD_INCOMPLETE", "Verified upload bytes are required.")
    try:
        await anyio.to_thread.run_sync(
            adapter.verify,
            adapter.path(context.organization_id, doc.id, row.id),
            doc.size_bytes,
            doc.checksum_sha256,
            doc.media_type,
        )
    except TimeoutError:
        raise ApiError(408, "UPLOAD_TIMEOUT", "Upload exceeded its time limit.") from None
    except OSError:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Private upload storage is unavailable."
        ) from None
    if not replayed:
        usage = await reserve(
            db,
            organization_id=context.organization_id,
            user_id=context.user_id,
            key=f"document-intake:{doc.id}",
            operation="document_intake",
            meter="document_uploads",
            quantity=1,
        )
        await settle(
            db, organization_id=context.organization_id, event_id=usage.id, state="succeeded"
        )
        row.status = "completed"
        row.completed_at = row.updated_at = now()
        doc.scan_status = "pending_scan"
        doc.updated_at = now()
        await db.commit()
    return success_response(request, {"attempt": public_attempt(row), "replayed": replayed})


@router.post("/{document_id}/extractions", dependencies=[Depends(origin), Depends(upload_slot)])
async def extract(
    document_id: UUID, data: CreateExtraction, request: Request, context: Write, db: DB, key: Key
):
    require_action(context, Action.EXTRACTION_REQUEST)
    doc = await document(db, context, document_id)
    require_document(context, Action.EXTRACTION_REQUEST, attributes(doc))
    raise ApiError(503, "EXTRACTOR_UNAVAILABLE", "No extraction provider is configured.")


@router.get("/{document_id}/extractions")
async def extraction_list(document_id: UUID, request: Request, context: Read, db: DB):
    await document(db, context, document_id)
    # Honest unavailable read: no persisted extraction subsystem exists yet.
    raise ApiError(503, "EXTRACTOR_UNAVAILABLE", "Extraction persistence is not implemented.")
