"""Python mapping of the existing Node audit_events contract (separate migration chain)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("length(action) BETWEEN 1 AND 120", name="audit_events_action_check"),
        CheckConstraint(
            "length(resource_type) BETWEEN 1 AND 120", name="audit_events_resource_type_check"
        ),
        Index("audit_events_org_created_idx", "organization_id", "created_at", "id"),
        Index("audit_events_actor_created_idx", "actor_user_id", "created_at"),
        Index(
            "audit_events_resource_idx",
            "organization_id",
            "resource_type",
            "resource_id",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(
        "metadata", JSON().with_variant(JSONB(), "postgresql"), default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
