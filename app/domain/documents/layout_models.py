"""Private, version-bound local layout work. Results always require human review."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LayoutJob(Base):
    __tablename__ = "document_layout_jobs"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_layout_tenant_identity"),
        UniqueConstraint("document_id", "pipeline_version", name="uq_layout_document_version"),
        ForeignKeyConstraint(["document_id", "organization_id"],
                             ["documents.id", "documents.organization_id"]),
        ForeignKeyConstraint(["actor_user_id", "organization_id"],
                             ["users.id", "users.organization_id"]),
        CheckConstraint("state IN ('pending','running','needs_review','error')",
                        name="ck_layout_state"),
        CheckConstraint("attempts >= 0 AND attempts <= 3", name="ck_layout_attempts"),
        CheckConstraint("page_count IS NULL OR (page_count > 0 AND page_count <= 20)",
                        name="ck_layout_pages"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column()
    upload_id: Mapped[UUID] = mapped_column()
    actor_user_id: Mapped[UUID] = mapped_column()
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_code: Mapped[str | None] = mapped_column(String(40))
    page_count: Mapped[int | None] = mapped_column(Integer)
    page_previews: Mapped[list | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
