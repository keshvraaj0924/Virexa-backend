"""Durable tracking records only; these tables do not represent job execution."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_workflow_tenant_identity"),
        ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_workflow_tenant_creator",
        ),
        CheckConstraint(
            "status IN ('draft','active','paused','archived')", name="ck_workflow_status"
        ),
        CheckConstraint("version > 0", name="ck_workflow_version"),
        CheckConstraint("length(name) BETWEEN 1 AND 120", name="ck_workflow_name"),
        Index("ix_workflow_tenant_created", "organization_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    created_by_user_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowKey(Base):
    __tablename__ = "workflow_idempotency_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "organization_id"],
            ["workflows.id", "workflows.organization_id"],
            name="fk_workflow_key_tenant",
        ),
    )
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    workflow_id: Mapped[UUID | None] = mapped_column()
