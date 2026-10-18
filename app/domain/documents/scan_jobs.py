"""Internal leased worker; no HTTP verdict mutation or extraction dispatch."""

import asyncio
import hashlib
import os
import stat
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.audit.service import record
from app.domain.documents.models import Document, ScanJob, Upload
from app.domain.documents.scanner import ClamAVScanner, ScanResult
from app.domain.documents.storage import QuarantineStorage


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def claim(db: AsyncSession) -> tuple[UUID, UUID] | None:
    """Four global slots, one per tenant. PostgreSQL is required for production."""
    if db.bind.dialect.name == "postgresql":
        # Serialize admission only, never hold this lock during byte reads/scanning.
        await db.execute(text("SELECT pg_advisory_xact_lock(867392401)"))
    timestamp = now()
    expired = list(
        await db.scalars(
            select(ScanJob)
            .where(
                ScanJob.state == "running", ScanJob.lease_until <= timestamp, ScanJob.attempts >= 3
            )
            .with_for_update()
        )
    )
    for job in expired:
        job.state = "error"
        job.result_code = "lease_exhausted"
        job.updated_at = timestamp
        await audit(db, job, "error")
    active = and_(ScanJob.state == "running", ScanJob.lease_until > timestamp)
    if await db.scalar(select(func.count()).select_from(ScanJob).where(active)) >= 4:
        await db.commit()
        return None
    busy_tenants = select(ScanJob.organization_id).where(active)
    job = await db.scalar(
        select(ScanJob)
        .where(
            ScanJob.attempts < 3,
            ScanJob.organization_id.not_in(busy_tenants),
            or_(
                and_(ScanJob.state == "pending", ScanJob.available_at <= timestamp),
                and_(ScanJob.state == "running", ScanJob.lease_until <= timestamp),
            ),
        )
        .order_by(ScanJob.available_at, ScanJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        await db.commit()
        return None
    job.state = "running"
    job.attempts += 1
    job.lease_token = uuid4()
    job.lease_until = timestamp + timedelta(seconds=90)
    job.updated_at = timestamp
    result = (job.id, job.lease_token)
    await db.commit()
    return result


async def audit(db: AsyncSession, job: ScanJob, verdict: str):
    await record(
        db,
        organization_id=job.organization_id,
        actor_user_id=job.actor_user_id,
        action=f"document.scan.{verdict}",
        request_id=str(uuid4()),
        resource_id=job.document_id,
    )


def read_snapshot(storage: QuarantineStorage, job: ScanJob, doc: Document) -> bytes:
    path = storage.path(job.organization_id, job.document_id, job.upload_id)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != doc.size_bytes:
            raise ValueError("Quarantine integrity failure")
        data = stream.read(26214401)
        after = os.fstat(stream.fileno())

        def identity(item):
            return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)

        if identity(info) != identity(after) or identity(after) != identity(path.lstat()):
            raise ValueError("Quarantine changed during read")
    if (
        len(data) != doc.size_bytes
        or not 0 < len(data) <= 26214400
        or hashlib.sha256(data).hexdigest() != job.checksum_sha256
        or doc.checksum_sha256 != job.checksum_sha256
    ):
        raise ValueError("Quarantine integrity failure")
    return data


async def run_once(
    factory: async_sessionmaker, storage: QuarantineStorage, socket_path: str | None
) -> bool:
    """Disabled means no claims. Configured scanner failure never marks clean."""
    if not socket_path:
        return False
    scanner = ClamAVScanner(socket_path)
    async with factory() as db:
        lease = await claim(db)
    if lease is None:
        return False
    job_id, token = lease
    async with factory() as db:
        job = await db.get(ScanJob, job_id)
        doc = await db.get(Document, job.document_id)
        upload = await db.get(Upload, job.upload_id)
        try:
            if (
                doc.organization_id != job.organization_id
                or doc.scan_status != "pending_scan"
                or upload.organization_id != job.organization_id
                or upload.document_id != doc.id
                or upload.status != "completed"
                or not upload.bytes_verified
            ):
                raise ValueError("Invalid scan source")
            data = await asyncio.to_thread(read_snapshot, storage, job, doc)
            result = await scanner.scan(data)
            # Revalidate private original before accepting the snapshot verdict.
            if result.verdict == "clean":
                await asyncio.to_thread(read_snapshot, storage, job, doc)
        except (OSError, ValueError):
            result = ScanResult("error", "integrity_failed")
        await db.rollback()
    async with factory() as db:
        job = await db.scalar(select(ScanJob).where(ScanJob.id == job_id).with_for_update())
        if job.state != "running" or job.lease_token != token or aware(job.lease_until) <= now():
            return True  # Stale worker can never publish a verdict.
        doc = await db.scalar(
            select(Document)
            .where(Document.id == job.document_id, Document.organization_id == job.organization_id)
            .with_for_update()
        )
        if doc.scan_status != "pending_scan" or doc.checksum_sha256 != job.checksum_sha256:
            result = ScanResult("error", "source_changed")
        job.result_code = result.code
        job.updated_at = now()
        job.lease_token = None
        job.lease_until = None
        if result.verdict == "error":
            job.state = "pending" if job.attempts < 3 else "error"
            job.available_at = now() + timedelta(seconds=60 * job.attempts)
        else:
            job.state = result.verdict
            doc.scan_status = result.verdict
            doc.updated_at = now()
        await audit(db, job, result.verdict)
        await db.commit()
    return True
