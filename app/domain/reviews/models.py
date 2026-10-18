"""Append-only human decisions; canonical parser results are never modified."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewEvent(Base):
    __tablename__ = "document_review_events"
    __table_args__ = (
        UniqueConstraint("job_id", "revision", name="uq_review_revision"),
        UniqueConstraint("job_id", "idempotency_key", name="uq_review_key"),
        ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["document_layout_jobs.id", "document_layout_jobs.organization_id"],
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
        CheckConstraint("status != 'approved' OR source_compared", name="ck_review_attestation"),
        CheckConstraint("revision > 0", name="ck_review_revision"),
        CheckConstraint("result_kind = 'layout'", name="ck_review_kind"),
        CheckConstraint("status IN ('corrected','approved','rejected')", name="ck_review_status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(index=True)
    job_id: Mapped[UUID] = mapped_column(index=True)
    actor_user_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    result_kind: Mapped[str] = mapped_column(String(20), default="layout")
    source_sha256: Mapped[str] = mapped_column(String(64))
    result_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    source_compared: Mapped[bool] = mapped_column(Boolean, default=False)
    reason_code: Mapped[str] = mapped_column(String(40))
    corrections: Mapped[list] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Database migrations additionally enforce this for direct SQL on PostgreSQL.
from sqlalchemy import event  # noqa: E402


@event.listens_for(ReviewEvent, "before_update")
@event.listens_for(ReviewEvent, "before_delete")
def immutable_review_history(mapper, connection, target):
    raise ValueError("Review history is immutable")
