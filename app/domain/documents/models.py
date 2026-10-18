from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_document_tenant_identity"),
        UniqueConstraint("organization_id", "checksum_sha256", name="uq_document_checksum"),
        UniqueConstraint("organization_id", "external_reference", name="uq_document_reference"),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 26214400", name="ck_document_size"),
        CheckConstraint(
            "status IN ('received','processing','review_required','completed','failed')",
            name="ck_document_status",
        ),
        CheckConstraint(
            "scan_status IN ('awaiting_upload','pending_scan','clean','rejected')",
            name="ck_document_scan",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    original_file_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    # Server-owned current candidate identity; never chosen by wall-clock ordering.
    current_layout_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="received")
    scan_status: Mapped[str] = mapped_column(String(30), default="awaiting_upload")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Upload(Base):
    __tablename__ = "document_uploads"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_upload_document"),
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_upload_tenant_document",
        ),
        CheckConstraint("status IN ('initiated','completed','failed')", name="ck_upload_status"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_upload_idempotency"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(255))
    bytes_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="initiated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScanJob(Base):
    __tablename__ = "document_scan_jobs"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_scan_document"),
        ForeignKeyConstraint(["document_id", "organization_id"],
                             ["documents.id", "documents.organization_id"]),
        ForeignKeyConstraint(["actor_user_id", "organization_id"],
                             ["users.id", "users.organization_id"]),
        CheckConstraint("state IN ('pending','running','clean','rejected','error')",
                        name="ck_scan_job_state"),
        CheckConstraint("attempts >= 0 AND attempts <= 3", name="ck_scan_job_attempts"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_id: Mapped[UUID] = mapped_column()
    upload_id: Mapped[UUID] = mapped_column(ForeignKey("document_uploads.id"))
    actor_user_id: Mapped[UUID] = mapped_column()
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_code: Mapped[str | None] = mapped_column(String(40))
